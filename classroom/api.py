"""FastAPI routes for Ghostwriter Classroom.

Mount this app at /classroom on a separate port (default 8081) so it
shares the cluster network with the main Ghostwriter service but is
addressable via its own OpenShift Route.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import session_manager as sm
from .grader import grade_all, surprise_me, moderate_story, surprise_me_detection
from .models import Phase, PeerVote, Rubric, Story, StoryArc, ModelConfig, SessionMode
from .traffic_generator import generate_intrusion_traffic

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/classroom", tags=["classroom"])


def _is_running_in_openshift() -> bool:
    """Detect if running inside OpenShift/Kubernetes cluster."""
    # Check for Kubernetes service account
    if os.path.exists('/var/run/secrets/kubernetes.io/serviceaccount/token'):
        return True
    # Check for Kubernetes environment variable
    if os.getenv('KUBERNETES_SERVICE_HOST'):
        return True
    return False


# ── Request / response schemas ────────────────────────────────────────────────

class SessionOut(BaseModel):
    id: str
    phase: str
    student_count: int
    mode: str = "storytelling"  # For backwards compatibility


class CreateSessionIn(BaseModel):
    mode: SessionMode = SessionMode.STORYTELLING


class JoinOut(BaseModel):
    username: str
    session_id: str


class StoryIn(BaseModel):
    hero_name: str
    hero_backstory: str
    challenge: str
    resolution: str
    surprise_me: bool = False

    def validate_not_empty(self) -> tuple[bool, str]:
        """Check that required fields are not empty. Returns (valid, error_message)."""
        if not self.hero_name.strip():
            return False, "Hero name cannot be empty"
        if not self.hero_backstory.strip():
            return False, "Hero backstory cannot be empty"
        if not self.challenge.strip():
            return False, "Challenge cannot be empty"
        if not self.resolution.strip():
            return False, "Resolution cannot be empty"
        return True, ""


class DetectionPromptIn(BaseModel):
    detection_text: str
    ai_generated: bool = False


class VoteIn(BaseModel):
    voter_username: str
    story_a_id: str
    story_b_id: str
    winner_id: str


class FinalVoteIn(BaseModel):
    voter_username: str
    story_id: str


# ── Configuration endpoint ───────────────────────────────────────────────────

@router.get("/config")
async def get_config():
    """Return runtime configuration for the frontend."""
    return {
        "kserve_available": _is_running_in_openshift(),
    }


@router.get("/models/ollama")
async def get_ollama_models():
    """Return list of locally available Ollama models, or popular defaults."""
    import subprocess

    # Popular Ollama models (fallback for cloud deployments)
    # Includes both local and :cloud variants
    popular_models = [
        # Small local models
        {"name": "llama3.2:3b", "label": "Llama 3.2 3B (local)"},
        {"name": "llama3.1:8b", "label": "Llama 3.1 8B (local)"},
        {"name": "qwen2.5:7b", "label": "Qwen 2.5 7B (local)"},
        {"name": "mistral:7b", "label": "Mistral 7B (local)"},
        {"name": "gemma2:9b", "label": "Gemma 2 9B (local)"},

        # Large cloud models (require :cloud suffix)
        {"name": "llama3.1:70b-cloud", "label": "Llama 3.1 70B (cloud)"},
        {"name": "llama3.3:70b-cloud", "label": "Llama 3.3 70B (cloud)"},
        {"name": "qwen2.5:14b-cloud", "label": "Qwen 2.5 14B (cloud)"},
        {"name": "qwen2.5:32b-cloud", "label": "Qwen 2.5 32B (cloud)"},
        {"name": "mixtral:8x7b-cloud", "label": "Mixtral 8x7B (cloud)"},
        {"name": "gemma2:27b-cloud", "label": "Gemma 2 27B (cloud)"},
        {"name": "deepseek-v4-pro:cloud", "label": "DeepSeek V4 Pro (cloud)"},
    ]

    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Parse output (skip header line)
            lines = result.stdout.strip().split('\n')[1:]
            models = []
            for line in lines:
                if line.strip():
                    # First column is the model name
                    parts = line.split()
                    if parts:
                        model_name = parts[0]
                        models.append({
                            "name": model_name,
                            "label": f"{model_name} (Ollama)"
                        })

            if models:
                logger.info(f"Found {len(models)} local Ollama models")
                return {"models": models}

        # Fallthrough to popular models
        logger.info("Using popular Ollama models (local ollama not available)")
        return {"models": popular_models}

    except (FileNotFoundError, Exception) as e:
        logger.info(f"Ollama not available locally, using popular models: {e}")
        return {"models": popular_models}


@router.get("/models/groq")
async def get_groq_models():
    """Return list of available Groq production models.

    Production models only (as of April 2026).
    See https://console.groq.com/docs/models
    """
    return {
        "models": [
            {"name": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B Versatile (280 T/s)"},
            {"name": "llama-3.1-8b-instant", "label": "Llama 3.1 8B Instant (560 T/s)"},
            {"name": "openai/gpt-oss-120b", "label": "GPT OSS 120B (500 T/s)"},
            {"name": "openai/gpt-oss-20b", "label": "GPT OSS 20B"},
        ]
    }


@router.get("/models/kserve")
async def get_kserve_models():
    """Return list of available KServe models in the cluster."""
    if not _is_running_in_openshift():
        return {"models": []}

    # Query KServe for actual deployed models using Kubernetes API
    try:
        import requests
        import json

        # Read service account token and CA cert
        token_path = '/var/run/secrets/kubernetes.io/serviceaccount/token'
        ca_cert_path = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'

        if not os.path.exists(token_path):
            logger.warning("Service account token not found - not running in cluster")
            return {"models": []}

        with open(token_path, 'r') as f:
            token = f.read().strip()

        # Kubernetes API server
        k8s_host = os.getenv('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')
        k8s_port = os.getenv('KUBERNETES_SERVICE_PORT', '443')

        # Query InferenceServices in ghostwriter namespace
        url = f"https://{k8s_host}:{k8s_port}/apis/serving.kserve.io/v1beta1/namespaces/ghostwriter/inferenceservices"
        headers = {'Authorization': f'Bearer {token}'}

        response = requests.get(url, headers=headers, verify=ca_cert_path, timeout=5)

        if response.status_code != 200:
            logger.warning(f"Failed to query InferenceServices: {response.status_code} {response.text}")
            return {"models": []}

        data = response.json()
        models = []
        for item in data.get("items", []):
            name = item.get("metadata", {}).get("name", "")
            # Check if the InferenceService is ready
            status = item.get("status", {})
            conditions = status.get("conditions", [])
            is_ready = any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in conditions
            )
            if is_ready:
                # Create a nice label from the name
                label = name.replace("-", " ").title() + " (KServe)"
                models.append({"name": name, "label": label})

        logger.info(f"Found {len(models)} ready KServe models")
        return {"models": models}
    except Exception as e:
        logger.error(f"Failed to get KServe models: {e}")
        return {"models": []}


# ── Session endpoints ─────────────────────────────────────────────────────────

@router.post("/sessions", response_model=SessionOut, status_code=201)
async def create_session(params: Optional[CreateSessionIn] = None):
    """Instructor creates a new session. Returns session ID to share with class."""
    mode = params.mode if params else SessionMode.STORYTELLING
    session = sm.create_session(mode=mode)
    return SessionOut(id=session.id, phase=session.phase, student_count=0, mode=session.mode)


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def get_session(session_id: str):
    session = sm.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return SessionOut(id=session.id, phase=session.phase,
                      student_count=len(session.students))


@router.get("/sessions/{session_id}/debug")
async def debug_session(session_id: str):
    """Debug endpoint to view full session state."""
    session = sm.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    result = {
        "id": session.id,
        "mode": session.mode,
        "phase": session.phase,
        "llm_config": session.llm_config.model_dump(),
        "students": list(session.students.keys()),
        "finalists": session.finalists,
        "ai_errors": session.ai_errors,
    }

    if session.mode == SessionMode.STORYTELLING:
        result["stories"] = {
            sid: {
                "id": story.id,
                "author": story.author_username,
                "hero": story.hero_name,
                "hero_backstory": story.hero_backstory,
                "challenge": story.challenge,
                "resolution": story.resolution,
                "peer_votes": story.peer_votes,
                "final_votes": story.final_votes,
                "ai_score_review": story.ai_score_review.model_dump() if story.ai_score_review else None,
                "ai_score_reveal": story.ai_score_reveal.model_dump() if story.ai_score_reveal else None,
            }
            for sid, story in session.stories.items()
        }
    elif session.mode == SessionMode.INTRUSION_DETECTION:
        result["detection_prompts"] = {
            pid: {
                "id": prompt.id,
                "author": prompt.author_username,
                "detection_text": prompt.detection_text,
                "ai_generated": prompt.ai_generated,
                "ai_score_review": prompt.ai_score_review.model_dump() if prompt.ai_score_review else None,
                "ai_score_reveal": prompt.ai_score_reveal.model_dump() if prompt.ai_score_reveal else None,
            }
            for pid, prompt in session.detection_prompts.items()
        }
        if session.network_traffic:
            result["traffic_summary"] = {
                "entry_count": len(session.network_traffic.entries),
                "attack_description": session.network_traffic.attack_description,
                "metadata": session.network_traffic.metadata
            }

    return result


@router.post("/sessions/{session_id}/join", response_model=JoinOut)
async def join_session(session_id: str):
    """Student joins and receives a unique username."""
    try:
        student = sm.add_student(session_id)
    except KeyError:
        raise HTTPException(404, "Session not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    await sm.broadcast(session_id, {
        "event": "student.joined",
        "username": student.username,
        "student_count": len(sm.require_session(session_id).students),
    })
    return JoinOut(username=student.username, session_id=session_id)


# ── Setup phase ───────────────────────────────────────────────────────────────

@router.put("/sessions/{session_id}/arc")
async def set_arc(session_id: str, arc: StoryArc):
    session = sm.require_session(session_id)
    session.story_arc = arc
    sm._save_session(session)
    await sm.broadcast(session_id, {"event": "arc.updated", "arc": arc.model_dump()})
    return {"ok": True}


@router.put("/sessions/{session_id}/rubric")
async def set_rubric(session_id: str, rubric: Rubric):
    session = sm.require_session(session_id)
    session.rubric = rubric
    sm._save_session(session)
    await sm.broadcast(session_id, {"event": "rubric.updated", "rubric": rubric.model_dump()})
    return {"ok": True}


@router.put("/sessions/{session_id}/model")
async def set_model(session_id: str, config: ModelConfig):
    session = sm.require_session(session_id)
    session.llm_config = config
    sm._save_session(session)
    await sm.broadcast(session_id, {"event": "model.updated", "model": config.model_dump()})
    return {"ok": True}


@router.put("/sessions/{session_id}/grading-model")
async def set_grading_model(session_id: str, config: ModelConfig):
    session = sm.require_session(session_id)
    session.grading_config = config
    sm._save_session(session)
    await sm.broadcast(session_id, {"event": "grading_model.updated", "model": config.model_dump()})
    return {"ok": True}


class WritingTimeIn(BaseModel):
    writing_seconds: int


@router.put("/sessions/{session_id}/writing-time")
async def set_writing_time(session_id: str, params: WritingTimeIn):
    """Configure the duration of the writing phase (in seconds)."""
    session = sm.require_session(session_id)
    if session.phase != Phase.SETUP:
        raise HTTPException(400, "Can only configure writing time during setup phase")
    session.writing_seconds = params.writing_seconds
    sm._save_session(session)
    await sm.broadcast(session_id, {"event": "writing_time.updated", "writing_seconds": params.writing_seconds})
    return {"ok": True}


@router.post("/sessions/{session_id}/start")
async def start_writing(session_id: str):
    """Instructor kicks off the writing phase."""
    await sm.start_writing(session_id)
    return {"ok": True}


class SimulateIn(BaseModel):
    student_count: int = 3
    story_delay: float = 3.0


@router.post("/sessions/{session_id}/simulate")
async def simulate_session(session_id: str, params: SimulateIn):
    """Simulate a full classroom session with AI-generated students and stories."""
    import asyncio
    asyncio.create_task(sm.run_simulation(session_id, params.student_count, params.story_delay))
    return {"ok": True, "message": f"Simulation started with {params.student_count} students"}


# ── Writing phase ─────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/stories/{username}")
async def submit_story(session_id: str, username: str, story_in: StoryIn):
    logger.info(f"Story submission from {username} in session {session_id}")
    logger.debug(f"Story data: hero_name={story_in.hero_name!r}, backstory_len={len(story_in.hero_backstory)}, challenge_len={len(story_in.challenge)}, resolution_len={len(story_in.resolution)}")

    # Validate not empty
    valid, error_msg = story_in.validate_not_empty()
    if not valid:
        logger.warning(f"Empty field validation failed for {username}: {error_msg}")
        raise HTTPException(400, error_msg)

    session = sm.require_session(session_id)
    if session.phase != Phase.WRITING:
        raise HTTPException(400, "Writing phase is not active")
    student = session.students.get(username)
    if not student:
        raise HTTPException(404, "Username not found in this session")
    if student.has_submitted:
        raise HTTPException(409, "Story already submitted")

    # Content moderation check
    approved, reason = await moderate_story(
        story_in.hero_name,
        story_in.hero_backstory,
        story_in.challenge,
        story_in.resolution,
        session.llm_config
    )

    if not approved:
        logger.warning(f"Story rejected for {username}: {reason}")
        raise HTTPException(
            400,
            f"Story content not appropriate: {reason}. Please revise and resubmit (must be PG-13 and no copyrighted characters)."
        )

    story = Story(
        author_username=username,
        hero_name=story_in.hero_name,
        hero_backstory=story_in.hero_backstory,
        challenge=story_in.challenge,
        resolution=story_in.resolution,
        surprise_me=story_in.surprise_me,
    )
    session.stories[story.id] = story
    student.has_submitted = True
    sm._save_session(session)

    submitted_count = sum(1 for s in session.students.values() if s.has_submitted)
    total = len(session.students)

    await sm.broadcast(session_id, {
        "event": "story.submitted",
        "username": username,
        "submitted_count": submitted_count,
        "total": total,
    })

    # Auto-advance to REVIEW if all students have submitted
    if submitted_count == total:
        logger.info(f"All {total} students submitted stories in session {session_id}, auto-advancing to REVIEW")
        await sm.start_review(session_id)

    return {"story_id": story.id}


@router.get("/sessions/{session_id}/surprise")
async def get_surprise(session_id: str):
    """Return an AI-generated story entry aligned with this session's arc."""
    session = sm.require_session(session_id)
    arc = session.story_arc
    data = await surprise_me(arc.step1, arc.step2, arc.step3, session.llm_config)
    return data


# ── Review phase ──────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/vote/peer")
async def peer_vote(session_id: str, vote: VoteIn):
    session = sm.require_session(session_id)
    if session.phase != Phase.REVIEW:
        raise HTTPException(400, "Not in review phase")
    pv = PeerVote(voter_username=vote.voter_username,
                  story_a_id=vote.story_a_id,
                  story_b_id=vote.story_b_id,
                  winner_id=vote.winner_id)
    await sm.record_peer_vote(session_id, pv)
    return {"ok": True}


@router.post("/sessions/{session_id}/grade/review")
async def grade_review(session_id: str):
    """Trigger AI grading of all stories (called automatically or by instructor)."""
    session = sm.require_session(session_id)
    # Use grading_config if set, otherwise fall back to llm_config
    grading_config = session.grading_config or session.llm_config
    scores, errors = await grade_all(session.stories, session.rubric, grading_config)
    logger.info(f"Received scores from grader: {scores}")

    # Track errors in session
    if errors:
        session.ai_errors.extend(errors)
        logger.error(f"Grading errors in session {session_id}: {errors}")

    for sid, score in scores.items():
        session.stories[sid].ai_score_review = score
        logger.info(f"Set story {sid} ai_score_review = {score}")
    logger.info(f"After assignment: {[(sid, s.ai_score_review) for sid, s in session.stories.items()]}")
    sm._save_session(session)

    # Broadcast results and errors
    await sm.broadcast(session_id, {
        "event": "ai.graded.review",
        "scores": {sid: score.model_dump() for sid, score in scores.items()},
        "errors": errors if errors else None,
    })
    return {"ok": True, "errors": errors}


# ── Final vote phase ──────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/vote/final")
async def final_vote(session_id: str, vote: FinalVoteIn):
    session = sm.require_session(session_id)
    if session.phase != Phase.FINAL_VOTE:
        raise HTTPException(400, "Not in final vote phase")
    await sm.record_final_vote(session_id, vote.voter_username, vote.story_id)
    return {"ok": True}


# ── Reveal phase ──────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/grade/reveal")
async def grade_reveal(session_id: str):
    """Final AI re-grade — called at the start of the reveal phase."""
    session = sm.require_session(session_id)
    # Use grading_config if set, otherwise fall back to llm_config
    grading_config = session.grading_config or session.llm_config
    scores, errors = await grade_all(session.stories, session.rubric, grading_config)

    # Track errors in session
    if errors:
        session.ai_errors.extend(errors)
        logger.error(f"Reveal grading errors in session {session_id}: {errors}")

    for sid, score in scores.items():
        session.stories[sid].ai_score_reveal = score
    sm._save_session(session)

    await sm.broadcast(session_id, {
        "event": "ai.graded.reveal",
        "scores": {sid: score.model_dump() for sid, score in scores.items()},
        "errors": errors if errors else None,
    })
    return {"ok": True, "errors": errors}


class PolishIn(BaseModel):
    rounds: int = 3


class PolishCompareIn(BaseModel):
    rounds: int = 3
    model1: ModelConfig
    model2: ModelConfig


@router.post("/sessions/{session_id}/polish")
async def polish_winner_story(session_id: str, params: PolishIn):
    """Iteratively polish the winning story using AI feedback."""
    import asyncio
    # Run in background so endpoint returns immediately
    asyncio.create_task(sm.polish_winner(session_id, params.rounds))
    return {"ok": True, "rounds": params.rounds}


@router.post("/sessions/{session_id}/polish-compare")
async def polish_compare_models(session_id: str, params: PolishCompareIn):
    """Compare two AI models polishing the same story."""
    import asyncio
    # Run in background so endpoint returns immediately
    asyncio.create_task(sm.polish_compare(session_id, params.rounds, params.model1, params.model2))
    return {"ok": True, "rounds": params.rounds}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@router.websocket("/sessions/{session_id}/ws")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    session = sm.get_session(session_id)
    if not session:
        await websocket.close(code=4004)
        return
    await websocket.accept()
    sm.register_ws(session_id, websocket)
    try:
        # Send current state immediately on connect
        await websocket.send_json({
            "event": "session.state",
            "phase": session.phase,
            "arc": session.story_arc.model_dump(),
            "rubric": session.rubric.model_dump(),
            "student_count": len(session.students),
            "writing_seconds": session.writing_seconds,
        })
        while True:
            await websocket.receive_text()   # keep-alive / ignore client messages
    except WebSocketDisconnect:
        sm.unregister_ws(session_id, websocket)


# ══════════════════════════════════════════════════════════════════════════════
# INTRUSION DETECTION MODE ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/sessions/{session_id}/generate-traffic")
async def generate_traffic(session_id: str, difficulty: str = "medium"):
    """Generate network traffic dataset for detection mode (SETUP phase only)."""
    session = sm.require_session(session_id)
    if session.mode != SessionMode.INTRUSION_DETECTION:
        raise HTTPException(400, "This endpoint is only for intrusion detection mode")
    if session.phase != Phase.SETUP:
        raise HTTPException(400, "Traffic can only be generated during SETUP phase")

    # Generate traffic using the instructor's LLM config
    traffic = await generate_intrusion_traffic(session.llm_config, difficulty)
    session.network_traffic = traffic
    sm._save_session(session)

    await sm.broadcast(session_id, {
        "event": "traffic.generated",
        "entry_count": len(traffic.entries),
        "difficulty": difficulty
    })

    logger.info(f"Generated {len(traffic.entries)} traffic entries for session {session_id}")
    return {"ok": True, "entries": len(traffic.entries), "difficulty": difficulty}


@router.get("/sessions/{session_id}/traffic")
async def get_traffic_data(session_id: str):
    """Return network traffic for students to analyze."""
    session = sm.require_session(session_id)
    if not session.network_traffic:
        raise HTTPException(404, "No traffic data has been generated yet")

    return {
        "entries": [e.model_dump() for e in session.network_traffic.entries],
        "metadata": session.network_traffic.metadata
    }


@router.post("/sessions/{session_id}/detections/{username}")
async def submit_detection(session_id: str, username: str, data: DetectionPromptIn):
    """Submit detection prompt (analogous to submit_story)."""
    logger.info(f"Detection submission from {username} in session {session_id}")
    logger.debug(f"Detection text length: {len(data.detection_text)}")

    session = sm.require_session(session_id)
    if session.mode != SessionMode.INTRUSION_DETECTION:
        raise HTTPException(400, "This endpoint is only for intrusion detection mode")
    if session.phase != Phase.WRITING:
        raise HTTPException(400, "Writing phase is not active")

    student = session.students.get(username)
    if not student:
        raise HTTPException(404, "Username not found in this session")
    if student.has_submitted:
        raise HTTPException(409, "Detection already submitted")

    # Validate not empty
    if not data.detection_text.strip():
        raise HTTPException(400, "Detection text cannot be empty")

    from .models import DetectionPrompt
    prompt = DetectionPrompt(
        author_username=username,
        detection_text=data.detection_text,
        ai_generated=data.ai_generated
    )
    session.detection_prompts[prompt.id] = prompt
    student.has_submitted = True
    sm._save_session(session)

    submitted_count = sum(1 for s in session.students.values() if s.has_submitted)
    total = len(session.students)

    await sm.broadcast(session_id, {
        "event": "detection.submitted",
        "username": username,
        "submitted_count": submitted_count,
        "total": total,
    })

    # Auto-advance to REVIEW if all students submitted
    if submitted_count == total:
        logger.info(f"All {total} students submitted detections in session {session_id}, auto-advancing to REVIEW")
        await sm.start_review(session_id)

    return {"prompt_id": prompt.id}


@router.get("/sessions/{session_id}/surprise-detection")
async def get_surprise_detection(session_id: str):
    """Generate a mediocre AI detection for 'Surprise Me' feature."""
    session = sm.require_session(session_id)
    if not session.network_traffic:
        raise HTTPException(404, "No traffic data available")

    data = await surprise_me_detection(session.network_traffic, session.llm_config)
    return data


@router.post("/sessions/{session_id}/polish-detection")
async def polish_detection_endpoint(session_id: str, rounds: int = 3):
    """Polish the best detection prompt iteratively."""
    import asyncio
    asyncio.create_task(sm.polish_winner_detection(session_id, rounds))
    return {"ok": True, "rounds": rounds}


@router.post("/sessions/{session_id}/polish-detection-compare")
async def polish_detection_compare_endpoint(session_id: str, params: PolishCompareIn):
    """Compare two AI models polishing the best detection."""
    import asyncio
    asyncio.create_task(sm.polish_detection_compare(session_id, params.rounds, params.model1, params.model2))
    return {"ok": True, "rounds": params.rounds}
