"""Session state machine for Ghostwriter Classroom.

Uses Redis when available (OpenShift), falls back to in-memory storage (Cloud Run).
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis

from .models import (
    Phase, Session, Story, Student, PeerVote, PolishResult, ModelConfig,
    SessionMode, DetectionPrompt, DetectionScore, NetworkTraffic
)
from .username_pool import generate_username
from .grader import (
    grade_all, grade_story, polish_story,
    grade_detection, grade_all_detections, polish_detection
)

logger = logging.getLogger(__name__)

# Try to connect to Redis, fall back to in-memory if unavailable
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
USE_REDIS = False
_redis = None
_memory_sessions: dict[str, str] = {}  # session_id → JSON string

try:
    _redis = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
    _redis.ping()
    USE_REDIS = True
    logger.info("Connected to Redis for session storage")
except Exception as e:
    logger.warning(f"Redis unavailable, using in-memory storage: {e}")
    USE_REDIS = False

# WebSocket connections (always in-memory, per-pod)
_websockets: dict[str, set] = {}    # session_id → set of connected WebSockets


# ── Session lifecycle ─────────────────────────────────────────────────────────

def _save_session(session: Session) -> None:
    """Persist session to Redis or in-memory storage."""
    key = f"session:{session.id}"
    data = session.model_dump_json()

    if USE_REDIS:
        _redis.set(key, data, ex=86400)  # 24 hour TTL
    else:
        _memory_sessions[session.id] = data


def create_session(mode: SessionMode = SessionMode.STORYTELLING) -> Session:
    session = Session(mode=mode)
    _save_session(session)
    _websockets[session.id] = set()
    logger.info("Created session %s mode=%s (storage: %s)", session.id, mode, "Redis" if USE_REDIS else "in-memory")
    return session


def get_session(session_id: str) -> Optional[Session]:
    if USE_REDIS:
        key = f"session:{session_id}"
        data = _redis.get(key)
    else:
        data = _memory_sessions.get(session_id)

    if not data:
        return None
    return Session.model_validate_json(data)


def require_session(session_id: str) -> Session:
    session = get_session(session_id)
    if not session:
        raise KeyError(f"Session {session_id!r} not found")
    return session


# ── Student management ────────────────────────────────────────────────────────

def add_student(session_id: str) -> Student:
    session = require_session(session_id)
    if session.phase != Phase.SETUP:
        raise ValueError("Cannot join after setup phase")
    username = generate_username(exclude=set(session.students))
    student = Student(username=username, session_id=session_id)
    session.students[username] = student
    _save_session(session)
    logger.info("Student %s joined session %s", username, session_id)
    return student


# ── Phase transitions ─────────────────────────────────────────────────────────

async def start_writing(session_id: str) -> None:
    session = require_session(session_id)

    # Validate that traffic exists for detection mode
    if session.mode == SessionMode.INTRUSION_DETECTION and not session.network_traffic:
        raise ValueError("Cannot start writing phase: network traffic has not been generated for detection mode")

    session.phase = Phase.WRITING
    session.writing_deadline = datetime.now(timezone.utc) + timedelta(seconds=session.writing_seconds)
    _save_session(session)
    await broadcast(session_id, {"event": "phase.writing",
                                  "deadline": session.writing_deadline.isoformat()})
    # Auto-advance when timer expires
    asyncio.create_task(_writing_timer(session_id, session.writing_seconds))


async def _writing_timer(session_id: str, seconds: int) -> None:
    await asyncio.sleep(seconds)
    session = get_session(session_id)
    if session and session.phase == Phase.WRITING:
        await start_review(session_id)


async def start_review(session_id: str) -> None:
    session = require_session(session_id)
    session.phase = Phase.REVIEW
    _save_session(session)

    if session.mode == SessionMode.STORYTELLING:
        # Assign 2 stories to each student for peer review
        story_ids = list(session.stories)
        assignments: dict[str, list[str]] = {}
        for username in session.students:
            candidates = [s for s in story_ids
                          if session.stories[s].author_username != username]
            sample = candidates[:2] if len(candidates) <= 2 else __import__('random').sample(candidates, 2)
            assignments[username] = sample
        await broadcast(session_id, {"event": "phase.review", "assignments": assignments})

        # Trigger AI grading in background
        asyncio.create_task(_auto_grade_review(session_id))

    elif session.mode == SessionMode.INTRUSION_DETECTION:
        # No peer voting - just AI grading
        await broadcast(session_id, {"event": "phase.review"})
        asyncio.create_task(_auto_grade_detections(session_id))
        # Auto-advance to REVEAL after grading completes
        asyncio.create_task(_auto_advance_to_reveal(session_id))


async def _auto_grade_review(session_id: str) -> None:
    """Background task to grade all stories when entering REVIEW phase."""
    try:
        session = get_session(session_id)
        if not session:
            logger.warning(f"Session {session_id} not found for auto-grading")
            return

        logger.info(f"Auto-grading {len(session.stories)} stories for session {session_id}")
        # Use grading_config if set, otherwise fall back to llm_config
        grading_config = session.grading_config or session.llm_config
        scores, errors = await grade_all(session.stories, session.rubric, grading_config)

        # Track errors
        if errors:
            session.ai_errors.extend(errors)
            logger.error(f"Auto-grading errors in session {session_id}: {errors}")

        # Update session with scores
        for sid, score in scores.items():
            if sid in session.stories:
                session.stories[sid].ai_score_review = score

        _save_session(session)
        logger.info(f"Auto-grading complete for session {session_id}")

        # Broadcast scores to all clients
        await broadcast(session_id, {
            "event": "ai.graded.review",
            "scores": {sid: score.model_dump() for sid, score in scores.items()},
            "errors": errors if errors else None,
        })
    except Exception as e:
        logger.error(f"Auto-grading failed for session {session_id}: {e}", exc_info=True)

async def _auto_grade_detections(session_id: str) -> None:
    """Background task to grade all detection prompts when entering REVIEW phase."""
    try:
        session = get_session(session_id)
        if not session:
            logger.warning(f"Session {session_id} not found for detection auto-grading")
            return

        logger.info(f"Auto-grading {len(session.detection_prompts)} detections for session {session_id}")
        grading_config = session.grading_config or session.llm_config

        scores, errors = await grade_all_detections(
            session.detection_prompts,
            session.network_traffic,
            session.rubric,
            grading_config
        )

        # Track errors
        if errors:
            session.ai_errors.extend(errors)
            logger.error(f"Detection grading errors in session {session_id}: {errors}")

        # Update session with scores
        for pid, score in scores.items():
            if pid in session.detection_prompts:
                session.detection_prompts[pid].ai_score_review = score

        _save_session(session)
        logger.info(f"Detection grading complete for session {session_id}")

        # Broadcast scores to all clients
        await broadcast(session_id, {
            "event": "ai.graded.review",
            "scores": {pid: score.model_dump() for pid, score in scores.items()},
            "errors": errors if errors else None,
        })
    except Exception as e:
        logger.error(f"Detection grading failed for session {session_id}: {e}", exc_info=True)


async def _auto_advance_to_reveal(session_id: str) -> None:
    """Auto-advance to REVEAL after detection grading completes."""
    # Wait a bit for grading to finish
    await asyncio.sleep(5)
    session = get_session(session_id)
    if session and session.phase == Phase.REVIEW and session.mode == SessionMode.INTRUSION_DETECTION:
        await reveal_detection_results(session_id)


async def reveal_detection_results(session_id: str) -> None:
    """Reveal phase for detection mode - show all scores and identify winner."""
    session = require_session(session_id)
    session.phase = Phase.REVEAL

    # Re-grade for final scores
    grading_config = session.grading_config or session.llm_config
    logger.info(f"Final grading for detection session {session_id}")

    try:
        scores, errors = await grade_all_detections(
            session.detection_prompts,
            session.network_traffic,
            session.rubric,
            grading_config
        )

        if errors:
            session.ai_errors.extend(errors)

        for pid, score in scores.items():
            if pid in session.detection_prompts:
                session.detection_prompts[pid].ai_score_reveal = score
    except Exception as e:
        logger.error(f"Final detection grading failed: {e}", exc_info=True)

    # Find best detection (highest composite score)
    if session.detection_prompts:
        best_prompt_id = max(
            session.detection_prompts.keys(),
            key=lambda pid: (
                session.detection_prompts[pid].ai_score_reveal or
                session.detection_prompts[pid].ai_score_review
            ).composite if (
                session.detection_prompts[pid].ai_score_reveal or
                session.detection_prompts[pid].ai_score_review
            ) else 0
        )
        session.ai_winner_id = best_prompt_id
    else:
        session.ai_winner_id = None

    _save_session(session)

    # Build leaderboard
    leaderboard = []
    for prompt in sorted(
        session.detection_prompts.values(),
        key=lambda p: (p.ai_score_reveal or p.ai_score_review).composite if (p.ai_score_reveal or p.ai_score_review) else 0,
        reverse=True
    ):
        score = prompt.ai_score_reveal or prompt.ai_score_review
        if score:
            leaderboard.append({
                "id": prompt.id,
                "author": prompt.author_username,
                "detection_preview": prompt.detection_text[:100] + "..." if len(prompt.detection_text) > 100 else prompt.detection_text,
                "accuracy": score.accuracy,
                "specificity": score.specificity,
                "clarity": score.clarity,
                "ai_composite": score.composite
            })

    await broadcast(session_id, {
        "event": "phase.reveal",
        "mode": "detection",
        "winner_id": session.ai_winner_id,
        "attack_description": session.network_traffic.attack_description if session.network_traffic else "",
        "leaderboard": leaderboard
    })


async def polish_winner_detection(session_id: str, rounds: int = 3) -> None:
    """Iteratively polish the winning detection using AI feedback."""
    session = require_session(session_id)

    if not session.ai_winner_id or session.ai_winner_id not in session.detection_prompts:
        logger.warning(f"No winner to polish in detection session {session_id}")
        return

    logger.info(f"Starting {rounds} rounds of polish for winner in session {session_id}")

    # Get original detection and score
    original = session.detection_prompts[session.ai_winner_id]
    original_score = original.ai_score_reveal or original.ai_score_review
    if not original_score:
        # Grade it if not already graded
        grading_config = session.grading_config or session.llm_config
        original_score, error = await grade_detection(
            original, session.network_traffic, session.rubric, grading_config
        )
        if error:
            session.ai_errors.append(error)

    # Create working copy
    current_prompt = DetectionPrompt(
        author_username=original.author_username,
        detection_text=original.detection_text,
        ai_generated=original.ai_generated
    )
    current_score = original_score

    # Run polish rounds
    for round_num in range(1, rounds + 1):
        logger.info(f"Polish round {round_num}/{rounds} - current score: {current_score.composite}")

        try:
            # Polish based on current score
            polished_data = await polish_detection(
                current_prompt, session.network_traffic, session.rubric, current_score, session.llm_config
            )

            # Create candidate with polished text
            candidate_prompt = DetectionPrompt(
                author_username=current_prompt.author_username,
                detection_text=polished_data.get("detection_text", current_prompt.detection_text),
                ai_generated=current_prompt.ai_generated
            )

            # Grade the candidate
            grading_config = session.grading_config or session.llm_config
            candidate_score, error = await grade_detection(
                candidate_prompt, session.network_traffic, session.rubric, grading_config
            )
            if error:
                session.ai_errors.append(error)

            # Only apply if score improved
            previous_score = current_score.composite
            if candidate_score.composite > current_score.composite:
                current_prompt = candidate_prompt
                current_score = candidate_score
                logger.info(f"Round {round_num}: Improved! {current_score.composite} (was {previous_score})")
            else:
                logger.info(f"Round {round_num}: No improvement ({current_score.composite} vs {candidate_score.composite})")

            # Broadcast progress
            await broadcast(session_id, {
                "event": "polish.progress",
                "round": round_num,
                "total_rounds": rounds,
                "score": current_score.model_dump(),
                "improved": candidate_score.composite > previous_score,
            })

            # Small delay between rounds
            if round_num < rounds:
                await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"Polish round {round_num} failed: {e}")

    # Broadcast completion
    await broadcast(session_id, {
        "event": "polish.complete",
        "author": original.author_username,
        "original": {
            "detection_text": original.detection_text,
            "score": original_score.model_dump(),
        },
        "polished": {
            "detection_text": current_prompt.detection_text,
            "score": current_score.model_dump(),
        },
    })

    logger.info(f"Polish complete: {original_score.composite} → {current_score.composite}")


async def polish_detection_compare(session_id: str, rounds: int, model1_config: ModelConfig, model2_config: ModelConfig) -> None:
    """Compare two AI models polishing the same detection in parallel."""
    session = require_session(session_id)

    if not session.ai_winner_id or session.ai_winner_id not in session.detection_prompts:
        logger.warning(f"No winner to polish in detection session {session_id}")
        return

    logger.info(f"Starting dual-model detection polish comparison ({rounds} rounds) for session {session_id}")

    # Get original detection and score
    original = session.detection_prompts[session.ai_winner_id]
    original_score = original.ai_score_reveal or original.ai_score_review
    if not original_score:
        # Grade it if not already graded
        grading_config = session.grading_config or session.llm_config
        original_score, error = await grade_detection(
            original, session.network_traffic, session.rubric, grading_config
        )
        if error:
            session.ai_errors.append(error)
            logger.error(f"Detection polish compare original grading error: {error}")

    # Helper function to polish with a single model
    async def polish_with_model(model_num: int, config: ModelConfig):
        current_prompt = DetectionPrompt(
            author_username=original.author_username,
            detection_text=original.detection_text,
            ai_generated=original.ai_generated
        )
        current_score = original_score

        for round_num in range(1, rounds + 1):
            logger.info(f"Detection Model {model_num} - Round {round_num}/{rounds} - score: {current_score.composite}")

            try:
                polished_data = await polish_detection(
                    current_prompt, session.network_traffic, session.rubric, current_score, config
                )
                candidate_prompt = DetectionPrompt(
                    author_username=current_prompt.author_username,
                    detection_text=polished_data.get("detection_text", current_prompt.detection_text),
                    ai_generated=current_prompt.ai_generated
                )

                # Use session's grading model (not the polish model) for scoring
                grading_config = session.grading_config or session.llm_config
                candidate_score, error = await grade_detection(
                    candidate_prompt, session.network_traffic, session.rubric, grading_config
                )
                if error:
                    session.ai_errors.append(error)
                    logger.error(f"Detection Model {model_num} round {round_num} grading error: {error}")

                previous_score = current_score.composite
                if candidate_score.composite > current_score.composite:
                    current_prompt = candidate_prompt
                    current_score = candidate_score
                    logger.info(f"Detection Model {model_num} Round {round_num}: Improved! {current_score.composite} (was {previous_score})")
                else:
                    logger.info(f"Detection Model {model_num} Round {round_num}: No improvement ({current_score.composite} vs {candidate_score.composite})")

                # Broadcast progress for this model
                progress_event = {
                    "event": f"polish.detection.progress.model{model_num}",
                    "round": round_num,
                    "total_rounds": rounds,
                    "score": current_score.model_dump(),
                }
                logger.info(f"Broadcasting detection progress for model {model_num}: {progress_event}")
                await broadcast(session_id, progress_event)

                if round_num < rounds:
                    await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Detection Model {model_num} Round {round_num} failed: {e}")

        return current_prompt, current_score

    # Run both models in parallel
    results = await asyncio.gather(
        polish_with_model(1, model1_config),
        polish_with_model(2, model2_config),
    )

    model1_prompt, model1_score = results[0]
    model2_prompt, model2_score = results[1]

    # Broadcast completion with both results
    completion_event = {
        "event": "polish.detection.compare.complete",
        "author": original.author_username,
        "original": {
            "detection_text": original.detection_text,
            "score": original_score.model_dump(),
        },
        "model1": {
            "detection_text": model1_prompt.detection_text,
            "score": model1_score.model_dump(),
        },
        "model2": {
            "detection_text": model2_prompt.detection_text,
            "score": model2_score.model_dump(),
        },
    }

    logger.info(f"Dual detection polish complete - Model1: {model1_score.composite} | Model2: {model2_score.composite}")
    await broadcast(session_id, completion_event)


async def record_peer_vote(session_id: str, vote: PeerVote) -> None:
    session = require_session(session_id)
    if vote.winner_id in session.stories:
        session.stories[vote.winner_id].peer_votes += 1
    student = session.students.get(vote.voter_username)
    if student:
        student.review_votes_cast += 1
    _save_session(session)

    # Broadcast vote progress
    votes_cast = sum(1 for s in session.students.values() if s.review_votes_cast >= 1)
    total_students = len(session.students)
    await broadcast(session_id, {
        "event": "vote.progress",
        "votes_cast": votes_cast,
        "total_students": total_students,
    })

    # Check if all students have voted → auto-advance
    # Each student casts 1 vote (picks favorite among other stories)
    if all(s.review_votes_cast >= 1 for s in session.students.values()):
        await promote_finalists(session_id)


async def promote_finalists(session_id: str) -> None:
    session = require_session(session_id)
    ranked = sorted(session.stories.values(),
                    key=lambda s: s.promotion_score, reverse=True)
    session.finalists = [s.id for s in ranked[:2]]
    session.phase = Phase.FINAL_VOTE
    _save_session(session)
    finalist_data = [
        {"id": sid, "author": session.stories[sid].author_username,
         "hero": session.stories[sid].hero_name}
        for sid in session.finalists
    ]
    await broadcast(session_id, {"event": "phase.final_vote", "finalists": finalist_data})


async def record_final_vote(session_id: str, voter: str, story_id: str) -> None:
    session = require_session(session_id)
    student = session.students.get(voter)
    if student and student.final_vote_cast is None and story_id in session.finalists:
        student.final_vote_cast = story_id
        session.stories[story_id].final_votes += 1
        _save_session(session)

        # Broadcast vote progress
        votes_cast = sum(1 for s in session.students.values() if s.final_vote_cast is not None)
        total_students = len(session.students)
        await broadcast(session_id, {
            "event": "final_vote.progress",
            "votes_cast": votes_cast,
            "total_students": total_students,
        })

    if all(s.final_vote_cast is not None for s in session.students.values()):
        await reveal(session_id)


async def reveal(session_id: str) -> None:
    session = require_session(session_id)
    session.phase = Phase.REVEAL

    # Auto-grade for final scores
    logger.info(f"Auto-grading reveal scores for session {session_id}")
    try:
        # Use grading_config if set, otherwise fall back to llm_config
        grading_config = session.grading_config or session.llm_config
        scores, errors = await grade_all(session.stories, session.rubric, grading_config)

        # Track errors
        if errors:
            session.ai_errors.extend(errors)
            logger.error(f"Reveal grading errors in session {session_id}: {errors}")

        for sid, score in scores.items():
            if sid in session.stories:
                session.stories[sid].ai_score_reveal = score
    except Exception as e:
        logger.error(f"Reveal auto-grading failed for session {session_id}: {e}", exc_info=True)

    # Human winner
    finalist_stories = [session.stories[sid] for sid in session.finalists]
    votes = {s.id: s.final_votes for s in finalist_stories}
    max_votes = max(votes.values())
    top = [sid for sid, v in votes.items() if v == max_votes]
    if len(top) == 1:
        session.winner_id = top[0]
    else:
        # Tiebreaker: AI score
        session.winner_id = max(top,
            key=lambda sid: (session.stories[sid].ai_score_reveal or
                             session.stories[sid].ai_score_review).composite
            if (session.stories[sid].ai_score_reveal or session.stories[sid].ai_score_review)
            else 0)

    # AI winner = story with highest final ai_score_reveal composite
    ai_scored = [s for s in session.stories.values() if s.ai_score_reveal]
    if ai_scored:
        session.ai_winner_id = max(ai_scored, key=lambda s: s.ai_score_reveal.composite).id

    _save_session(session)
    await broadcast(session_id, {
        "event": "phase.reveal",
        "winner_id": session.winner_id,
        "ai_winner_id": session.ai_winner_id,
        "leaderboard": [
            {"id": s.id, "author": s.author_username, "hero": s.hero_name,
             "peer_votes": s.peer_votes, "final_votes": s.final_votes,
             "ai_composite": s.ai_score_reveal.composite if s.ai_score_reveal else None}
            for s in sorted(session.stories.values(),
                            key=lambda x: x.final_votes, reverse=True)
        ]
    })


async def polish_winner(session_id: str, rounds: int = 3) -> None:
    """Iteratively polish the winning story using AI grading feedback."""
    session = require_session(session_id)

    if not session.winner_id or session.winner_id not in session.stories:
        logger.warning(f"No winner to polish in session {session_id}")
        return

    logger.info(f"Starting {rounds} rounds of polish for winner in session {session_id}")

    # Get original story and score
    original = session.stories[session.winner_id]
    original_score = original.ai_score_reveal or original.ai_score_review
    if not original_score:
        # Grade it if not already graded (use grading model)
        grading_config = session.grading_config or session.llm_config
        original_score, error = await grade_story(original, session.rubric, grading_config)
        if error:
            session.ai_errors.append(error)
            logger.error(f"Polish original grading error: {error}")

    # Create working copy for polishing
    current_story = Story(
        author_username=original.author_username,
        hero_name=original.hero_name,
        hero_backstory=original.hero_backstory,
        challenge=original.challenge,
        resolution=original.resolution,
    )
    current_score = original_score

    # Run polish rounds
    for round_num in range(1, rounds + 1):
        logger.info(f"Polish round {round_num}/{rounds} - current score: {current_score.composite}")

        try:
            # Polish based on current score
            polished_data = await polish_story(current_story, session.rubric, current_score, session.llm_config)

            # Create candidate story with polished content
            candidate_story = Story(
                author_username=current_story.author_username,
                hero_name=polished_data.get("hero_name", current_story.hero_name),
                hero_backstory=polished_data.get("hero_backstory", current_story.hero_backstory),
                challenge=polished_data.get("challenge", current_story.challenge),
                resolution=polished_data.get("resolution", current_story.resolution),
            )

            # Grade the candidate (use grading model)
            grading_config = session.grading_config or session.llm_config
            candidate_score, error = await grade_story(candidate_story, session.rubric, grading_config)
            if error:
                session.ai_errors.append(error)
                logger.error(f"Polish round {round_num} grading error: {error}")

            # Only apply changes if score improved
            previous_score = current_score.composite
            if candidate_score.composite > current_score.composite:
                current_story = candidate_story
                current_score = candidate_score
                logger.info(f"Round {round_num}: Improvement! {current_score.composite} (was {previous_score})")
            else:
                logger.info(f"Round {round_num}: No improvement. Keeping previous version ({current_score.composite} vs {candidate_score.composite})")

            # Broadcast progress
            progress_event = {
                "event": "polish.progress",
                "round": round_num,
                "total_rounds": rounds,
                "score": current_score.model_dump(),
                "improved": candidate_score.composite > current_score.composite,
            }
            await broadcast(session_id, progress_event)

            # Small delay between rounds to avoid rate limiting
            if round_num < rounds:
                await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"Polish round {round_num} failed: {e}")
            # Continue with next round anyway

    # Store result
    session.polish_result = PolishResult(
        original_story=original,
        polished_story=current_story,
        original_score=original_score,
        polished_score=current_score,
        rounds=rounds,
    )
    _save_session(session)

    # Broadcast completion
    completion_event = {
        "event": "polish.complete",
        "author": original.author_username,
        "original": {
            "hero": original.hero_name,
            "hero_backstory": original.hero_backstory,
            "challenge": original.challenge,
            "resolution": original.resolution,
            "score": original_score.model_dump(),
        },
        "polished": {
            "hero": current_story.hero_name,
            "hero_backstory": current_story.hero_backstory,
            "challenge": current_story.challenge,
            "resolution": current_story.resolution,
            "score": current_score.model_dump(),
        },
    }

    logger.info(f"Polish complete: {original_score.composite} → {current_score.composite}")
    logger.info(f"Broadcasting completion event: {completion_event}")

    await broadcast(session_id, completion_event)


async def polish_compare(session_id: str, rounds: int, model1_config: ModelConfig, model2_config: ModelConfig) -> None:
    """Compare two AI models polishing the same story in parallel."""
    session = require_session(session_id)

    if not session.winner_id or session.winner_id not in session.stories:
        logger.warning(f"No winner to polish in session {session_id}")
        return

    logger.info(f"Starting dual-model polish comparison ({rounds} rounds) for session {session_id}")

    # Get original story and score
    original = session.stories[session.winner_id]
    original_score = original.ai_score_reveal or original.ai_score_review
    if not original_score:
        # Use grading model for scoring
        grading_config = session.grading_config or session.llm_config
        original_score, error = await grade_story(original, session.rubric, grading_config)
        if error:
            session.ai_errors.append(error)
            logger.error(f"Polish compare original grading error: {error}")

    # Helper function to polish with a single model
    async def polish_with_model(model_num: int, config: ModelConfig):
        current_story = Story(
            author_username=original.author_username,
            hero_name=original.hero_name,
            hero_backstory=original.hero_backstory,
            challenge=original.challenge,
            resolution=original.resolution,
        )
        current_score = original_score

        for round_num in range(1, rounds + 1):
            logger.info(f"Model {model_num} - Round {round_num}/{rounds} - score: {current_score.composite}")

            try:
                polished_data = await polish_story(current_story, session.rubric, current_score, config)
                candidate_story = Story(
                    author_username=current_story.author_username,
                    hero_name=polished_data.get("hero_name", current_story.hero_name),
                    hero_backstory=polished_data.get("hero_backstory", current_story.hero_backstory),
                    challenge=polished_data.get("challenge", current_story.challenge),
                    resolution=polished_data.get("resolution", current_story.resolution),
                )

                # Use session's grading model (not the polish model) for scoring
                grading_config = session.grading_config or session.llm_config
                candidate_score, error = await grade_story(candidate_story, session.rubric, grading_config)
                if error:
                    session.ai_errors.append(error)
                    logger.error(f"Model {model_num} round {round_num} grading error: {error}")

                previous_score = current_score.composite
                if candidate_score.composite > current_score.composite:
                    current_story = candidate_story
                    current_score = candidate_score
                    logger.info(f"Model {model_num} Round {round_num}: Improved! {current_score.composite} (was {previous_score})")
                else:
                    logger.info(f"Model {model_num} Round {round_num}: No improvement ({current_score.composite} vs {candidate_score.composite})")

                # Broadcast progress for this model
                progress_event = {
                    "event": f"polish.progress.model{model_num}",
                    "round": round_num,
                    "total_rounds": rounds,
                    "score": current_score.model_dump(),
                }
                logger.info(f"Broadcasting progress for model {model_num}: {progress_event}")
                await broadcast(session_id, progress_event)

                if round_num < rounds:
                    await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Model {model_num} Round {round_num} failed: {e}")

        return current_story, current_score

    # Run both models in parallel
    results = await asyncio.gather(
        polish_with_model(1, model1_config),
        polish_with_model(2, model2_config),
    )

    model1_story, model1_score = results[0]
    model2_story, model2_score = results[1]

    # Broadcast completion with both results
    completion_event = {
        "event": "polish.compare.complete",
        "author": original.author_username,
        "original": {
            "hero": original.hero_name,
            "hero_backstory": original.hero_backstory,
            "challenge": original.challenge,
            "resolution": original.resolution,
            "score": original_score.model_dump(),
        },
        "model1": {
            "hero": model1_story.hero_name,
            "hero_backstory": model1_story.hero_backstory,
            "challenge": model1_story.challenge,
            "resolution": model1_story.resolution,
            "score": model1_score.model_dump(),
        },
        "model2": {
            "hero": model2_story.hero_name,
            "hero_backstory": model2_story.hero_backstory,
            "challenge": model2_story.challenge,
            "resolution": model2_story.resolution,
            "score": model2_score.model_dump(),
        },
    }

    logger.info(f"Dual polish complete - Model1: {model1_score.composite} | Model2: {model2_score.composite}")
    await broadcast(session_id, completion_event)


# ── Simulation ────────────────────────────────────────────────────────────────

async def run_simulation(session_id: str, student_count: int, story_delay: float = 3.0) -> None:
    """Simulate a full classroom session with AI students and stories/detections."""
    from .grader import surprise_me, surprise_me_detection

    try:
        session = get_session(session_id)
        if not session:
            logger.error(f"Simulation: Session {session_id} not found")
            return

        logger.info(f"Starting simulation for session {session_id} ({session.mode}) with {student_count} students, {story_delay}s delay")

        # Add students
        students = []
        for _ in range(student_count):
            student = add_student(session_id)
            students.append(student)
            await asyncio.sleep(0.5)

        logger.info(f"Simulation: Added {len(students)} students")

        # For detection mode, generate traffic before starting writing phase
        session = require_session(session_id)
        if session.mode == SessionMode.INTRUSION_DETECTION:
            logger.info(f"Simulation: Generating network traffic for detection mode")
            from .traffic_generator import generate_intrusion_traffic
            traffic = await generate_intrusion_traffic(session.llm_config, "medium")
            session.network_traffic = traffic
            _save_session(session)
            logger.info(f"Simulation: Generated {len(traffic.entries)} traffic entries")

        # Start writing phase
        await start_writing(session_id)
        await asyncio.sleep(2)

        # Generate and submit stories or detections based on mode
        session = require_session(session_id)

        # Detection mode simulation
        if session.mode == SessionMode.INTRUSION_DETECTION:
            logger.info(f"Simulation: Running detection mode for {len(students)} students")

            for i, student in enumerate(students):
                try:
                    # Generate detection script
                    detection_data = await surprise_me_detection(
                        session.network_traffic,
                        session.llm_config
                    )

                    # Create detection prompt object
                    from .models import DetectionPrompt
                    prompt = DetectionPrompt(
                        author_username=student.username,
                        detection_text=detection_data.get('detection_text', 'ALERT if suspicious activity detected'),
                        ai_generated=True
                    )

                    session.detection_prompts[prompt.id] = prompt
                    student.has_submitted = True
                    _save_session(session)

                    submitted_count = sum(1 for s in session.students.values() if s.has_submitted)
                    await broadcast(session_id, {
                        "event": "detection.submitted",
                        "username": student.username,
                        "submitted_count": submitted_count,
                        "total": len(session.students),
                    })

                    # Delay between generations
                    await asyncio.sleep(story_delay)

                except Exception as e:
                    logger.error(f"Simulation: Failed to generate detection for {student.username}: {e}")
                    # Fallback detection
                    from .models import DetectionPrompt
                    prompt = DetectionPrompt(
                        author_username=student.username,
                        detection_text="ALERT if [source_ip_count > 3] AND [dest_port_count > 5]",
                        ai_generated=True
                    )
                    session.detection_prompts[prompt.id] = prompt
                    student.has_submitted = True
                    _save_session(session)

            logger.info(f"Simulation: All detection scripts submitted")

            # Auto-advance to REVIEW for detection mode
            await asyncio.sleep(3)
            session = get_session(session_id)
            if session and session.phase != Phase.REVIEW:
                await start_review(session_id)

            # Detection mode auto-advances to REVEAL after grading
            logger.info(f"Simulation: Detection mode complete - waiting for auto-advance to REVEAL")
            return

        # Storytelling mode simulation (original code)
        session = require_session(session_id)

        # Define different story archetypes for variety
        archetypes = [
            {
                "style": "brilliant Sherlock Holmes-style detective",
                "approach": "extraordinary deductive reasoning and minute forensic details",
                "quality": "Make this story compelling with vivid details and clever twists"
            },
            {
                "style": "young amateur sleuth with minimal experience",
                "approach": "stumbling onto clues mostly by luck rather than skill",
                "quality": "Make this story simpler and less polished, with some naive reasoning"
            },
            {
                "style": "hardboiled noir detective",
                "approach": "tough interrogation tactics and street-level investigation",
                "quality": "Create a gritty, atmospheric story with strong character voice"
            },
            {
                "style": "eccentric elderly aristocrat detective",
                "approach": "understanding social dynamics and psychology of suspects",
                "quality": "Focus on character interactions and social observations"
            },
            {
                "style": "bumbling accidental investigator",
                "approach": "accidentally discovering evidence while trying to avoid trouble",
                "quality": "Make this story lighter and less serious, with unintentional discoveries"
            },
            {
                "style": "grief-stricken family member seeking justice",
                "approach": "personal knowledge of the victim's life and relationships",
                "quality": "Create an emotional story with personal stakes"
            },
            {
                "style": "methodical veteran detective",
                "approach": "systematic evidence collection and patient witness interviews",
                "quality": "Write a procedural-style story focusing on investigative steps"
            },
            {
                "style": "tech-savvy young investigator",
                "approach": "using modern forensic techniques and digital evidence",
                "quality": "Include contemporary investigation methods and fresh perspective"
            }
        ]

        for i, student in enumerate(students):
            try:
                # Assign different archetype to each student for variety
                archetype = archetypes[i % len(archetypes)]

                # Generate story with variation
                modified_step1 = (
                    f"{session.story_arc.step1}. The hero is a {archetype['style']} "
                    f"who investigates using {archetype['approach']}. {archetype['quality']}."
                )

                story_data = await surprise_me(
                    modified_step1,
                    session.story_arc.step2,
                    session.story_arc.step3,
                    session.llm_config
                )

                # Create story object
                from .models import Story
                story = Story(
                    author_username=student.username,
                    hero_name=story_data.get('hero_name', 'Hero'),
                    hero_backstory=story_data.get('hero_backstory', 'A brave soul'),
                    challenge=story_data.get('challenge', 'A difficult challenge'),
                    resolution=story_data.get('resolution', 'They overcame it'),
                    surprise_me=True
                )

                session.stories[story.id] = story
                student.has_submitted = True
                _save_session(session)

                submitted_count = sum(1 for s in session.students.values() if s.has_submitted)
                await broadcast(session_id, {
                    "event": "story.submitted",
                    "username": student.username,
                    "submitted_count": submitted_count,
                    "total": len(session.students),
                })

                # Delay between story generations to avoid rate limiting
                await asyncio.sleep(story_delay)

            except Exception as e:
                logger.error(f"Simulation: Failed to generate story for {student.username}: {e}")
                # Still create a fallback story so simulation continues
                from .models import Story
                story = Story(
                    author_username=student.username,
                    hero_name="Unnamed Hero",
                    hero_backstory="A mysterious figure",
                    challenge="They faced adversity",
                    resolution="They persevered",
                    surprise_me=True
                )
                session.stories[story.id] = story
                student.has_submitted = True
                _save_session(session)

        logger.info(f"Simulation: All stories submitted")

        # Auto-advance happens automatically via submit_story logic
        # Wait for review phase
        await asyncio.sleep(3)
        session = get_session(session_id)
        if session and session.phase != Phase.REVIEW:
            await start_review(session_id)

        # Wait for AI grading - calculate based on batch size and student count
        # Batches of 3 with 2s delays between batches, plus buffer for API processing
        grading_wait_time = (student_count / 3) * 2 + 30  # 30s buffer for API processing
        logger.info(f"Simulation: Waiting {grading_wait_time:.1f}s for AI grading of {student_count} stories")
        await asyncio.sleep(grading_wait_time)

        # Simulate peer voting
        session = require_session(session_id)
        story_ids = list(session.stories.keys())
        for student in students:
            # Find stories not by this student
            other_stories = [sid for sid in story_ids
                           if session.stories[sid].author_username != student.username]

            if len(other_stories) >= 1:
                # Each student votes once for their favorite among other stories
                # Pick a random favorite (in real scenario, students choose)
                import random
                winner_id = random.choice(other_stories)
                other_id = [s for s in other_stories if s != winner_id][0] if len(other_stories) > 1 else winner_id

                vote = PeerVote(
                    voter_username=student.username,
                    story_a_id=winner_id,
                    story_b_id=other_id,
                    winner_id=winner_id
                )
                await record_peer_vote(session_id, vote)
                await asyncio.sleep(0.5)

        logger.info(f"Simulation: Peer voting complete")

        # Wait for final vote phase
        await asyncio.sleep(2)

        # Simulate final votes
        session = require_session(session_id)
        if session.phase == Phase.FINAL_VOTE and session.finalists:
            winner_id = session.finalists[0]
            for student in students:
                await record_final_vote(session_id, student.username, winner_id)
                await asyncio.sleep(0.5)

        logger.info(f"Simulation complete for session {session_id}")

    except Exception as e:
        logger.error(f"Simulation failed for session {session_id}: {e}", exc_info=True)


# ── WebSocket broadcast ───────────────────────────────────────────────────────

def register_ws(session_id: str, ws) -> None:
    _websockets.setdefault(session_id, set()).add(ws)


def unregister_ws(session_id: str, ws) -> None:
    _websockets.get(session_id, set()).discard(ws)


async def broadcast(session_id: str, payload: dict) -> None:
    import json
    msg = json.dumps(payload)
    dead = set()
    for ws in list(_websockets.get(session_id, [])):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    for ws in dead:
        unregister_ws(session_id, ws)
