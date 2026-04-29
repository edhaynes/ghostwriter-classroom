"""AI grading for Ghostwriter Classroom stories via KServe / vLLM."""

import json
import logging
import os
import re
from typing import Optional

from openai import AsyncOpenAI

from .models import AIScore, Rubric, Story, ModelConfig, DetectionPrompt, DetectionScore, NetworkTraffic

logger = logging.getLogger(__name__)


def _get_client(config: ModelConfig) -> AsyncOpenAI:
    """Create OpenAI client from model config."""
    return AsyncOpenAI(
        base_url=config.endpoint,
        api_key=config.api_key or "unused",
        timeout=120.0,
    )


GRADING_SYSTEM = """You are a strict story judge for a classroom creative writing exercise.
These are quick student drafts written in 3 minutes, not polished work. Grade critically with high standards.

GRADING SCALE:
- 1-3: Poor quality, incomplete, or problematic
- 4-7: Average to good student work (MOST stories should land here)
- 8-9: Exceptional work showing professional-level creativity
- 10: Nearly perfect, publishable quality (extremely rare)

Use decimal precision (e.g., 5.6, 6.3, 7.8) to differentiate between stories.
Be tough - scores above 7 should be rare and only for truly outstanding work.

Respond ONLY with a valid JSON object matching this schema exactly (no markdown, no explanation):
{"criterion1": <float 1-10>, "criterion2": <float 1-10>, "criterion3": <float 1-10>}"""


def _build_prompt(story: Story, rubric: Rubric) -> str:
    return f"""Rubric:
- Criterion 1: {rubric.criterion1}
- Criterion 2: {rubric.criterion2}
- Criterion 3: {rubric.criterion3}

Story:
Hero: {story.hero_name} — {story.hero_backstory}
Challenge: {story.challenge}
Resolution: {story.resolution}

Grade each criterion from 1 (lowest) to 10 (highest)."""


async def grade_story(story: Story, rubric: Rubric, config: ModelConfig) -> tuple[AIScore, Optional[str]]:
    """Grade a story. Returns (score, error_message).

    error_message is None on success, or contains the error on failure.
    """
    prompt = _build_prompt(story, rubric)
    client = _get_client(config)
    try:
        logger.debug(f"Starting grade for story {story.id} with {config.provider}/{config.model_name}")
        resp = await client.chat.completions.create(
            model=config.model_name,
            messages=[
                {"role": "system", "content": GRADING_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=512,
        )
        raw = resp.choices[0].message.content or ""
        raw = raw.strip()
        logger.debug(f"Grading response for {story.id}: {raw[:100]}")
        # Tolerate minor formatting: extract first JSON object
        match = re.search(r"\{[^}]+\}", raw)
        if not match:
            raise ValueError(f"No JSON found in response: {raw!r}")
        data = json.loads(match.group())
        score = AIScore(
            criterion1=float(data.get("criterion1", 5)),
            criterion2=float(data.get("criterion2", 5)),
            criterion3=float(data.get("criterion3", 5)),
        )
        logger.debug(f"Story {story.id} graded: {score.composite}")
        return score, None
    except Exception as exc:
        error_msg = f"AI grading failed ({config.provider}/{config.model_name}): {str(exc)}"
        logger.warning("Grading failed for story %s: %s", story.id, exc)
        return AIScore(criterion1=5.0, criterion2=5.0, criterion3=5.0), error_msg


async def grade_all(stories: dict[str, Story], rubric: Rubric, config: ModelConfig) -> tuple[dict[str, AIScore], list[str]]:
    """Grade every story with rate limiting.

    Returns (scores, errors) where:
    - scores: mapping story_id → AIScore
    - errors: list of error messages from failed gradings
    """
    import asyncio

    # Process stories in batches to avoid rate limiting
    story_items = list(stories.items())
    batch_size = 3
    all_scores = {}
    all_errors = []

    for i in range(0, len(story_items), batch_size):
        batch = story_items[i:i + batch_size]
        logger.info(f"Grading batch {i//batch_size + 1}: {len(batch)} stories")

        tasks = {sid: asyncio.create_task(grade_story(story, rubric, config))
                 for sid, story in batch}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for sid, res in zip(tasks, results):
            if isinstance(res, Exception):
                all_scores[sid] = AIScore()
                all_errors.append(f"Story {sid}: {str(res)}")
            elif isinstance(res, tuple):
                score, error = res
                all_scores[sid] = score
                if error:
                    all_errors.append(error)
            else:
                # Shouldn't happen, but handle legacy case
                all_scores[sid] = res

        # Small delay between batches to avoid rate limiting
        if i + batch_size < len(story_items):
            await asyncio.sleep(2)

    logger.info(f"Graded {len(all_scores)} stories total, {len(all_errors)} errors")
    return all_scores, all_errors


SURPRISE_SYSTEM = """You are a creative writing assistant for a classroom story game.
Generate a unique, imaginative short story with exactly four fields.
Respond ONLY with valid JSON matching this schema (no markdown):
{
  "hero_name": "<unique name>",
  "hero_backstory": "<one sentence>",
  "challenge": "<one sentence>",
  "resolution": "<one sentence describing how hero overcame it and their reward>"
}
Be wildly creative and diverse! Use different genres, settings, tones, and character types each time.
Examples: space explorer, medieval baker, underwater detective, robot chef, time-traveling librarian, etc.
Keep each field to one sentence max."""


async def surprise_me(arc_step1: str, arc_step2: str, arc_step3: str, config: ModelConfig) -> dict:
    """Generate a surprise story entry aligned with the session arc."""
    import random

    # Add random genre/theme to increase variety
    themes = [
        "sci-fi adventure", "fantasy quest", "detective mystery", "western showdown",
        "space opera", "steampunk intrigue", "cyberpunk heist", "historical drama",
        "superhero origin", "noir thriller", "post-apocalyptic survival", "magical realism",
        "underwater exploration", "time travel paradox", "robot uprising", "pirate adventure",
        "haunted mansion", "lost civilization", "alien first contact", "dimension hopping"
    ]
    theme = random.choice(themes)

    prompt = (f"Generate a {theme} story that loosely follows this arc:\n"
              f"1. {arc_step1}\n"
              f"2. {arc_step2}\n"
              f"3. {arc_step3}\n\n"
              f"Be creative and unexpected! Don't copy the arc literally - interpret it in a unique way.")
    client = _get_client(config)
    try:
        resp = await client.chat.completions.create(
            model=config.model_name,
            messages=[
                {"role": "system", "content": SURPRISE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=1.0,
            max_tokens=512,
        )
        raw = resp.choices[0].message.content or ""
        raw = raw.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("No JSON in surprise response")
        return json.loads(match.group())
    except Exception as exc:
        logger.warning("Surprise Me failed: %s", exc)
        return {
            "hero_name": "Unnamed Hero",
            "hero_backstory": "A wanderer with an uncertain past and a hopeful future.",
            "challenge": "They faced an obstacle that tested everything they knew.",
            "resolution": "Through courage and wit they prevailed, earning respect and freedom.",
        }


POLISH_SYSTEM = """You are a creative writing coach improving student stories.
Given a story and its grading feedback, rewrite it to address weaknesses while keeping the core narrative.
Respond ONLY with valid JSON matching this schema (no markdown):
{
  "hero_name": "<name>",
  "hero_backstory": "<one improved sentence>",
  "challenge": "<one improved sentence>",
  "resolution": "<one improved sentence>"
}"""


async def polish_story(story: Story, rubric: Rubric, score: AIScore, config: ModelConfig) -> dict:
    """Rewrite a story based on grading feedback to improve scores."""
    feedback = []
    if score.criterion1 < 8:
        feedback.append(f"{rubric.criterion1} (score: {score.criterion1}/10)")
    if score.criterion2 < 8:
        feedback.append(f"{rubric.criterion2} (score: {score.criterion2}/10)")
    if score.criterion3 < 8:
        feedback.append(f"{rubric.criterion3} (score: {score.criterion3}/10)")

    feedback_text = "\n- ".join(feedback) if feedback else "Story is strong, refine the language and imagery."

    prompt = f"""Current Story:
Hero: {story.hero_name} — {story.hero_backstory}
Challenge: {story.challenge}
Resolution: {story.resolution}

Grading Rubric:
1. {rubric.criterion1}
2. {rubric.criterion2}
3. {rubric.criterion3}

Areas to improve:
- {feedback_text}

Rewrite this story to improve these areas while maintaining the core narrative."""

    client = _get_client(config)
    try:
        resp = await client.chat.completions.create(
            model=config.model_name,
            messages=[
                {"role": "system", "content": POLISH_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=512,
        )
        raw = resp.choices[0].message.content or ""
        raw = raw.strip()
        logger.debug(f"Polish response: {raw[:200]}")

        # Strip markdown code blocks if present
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if not match:
            logger.warning(f"No JSON found in polish response: {raw[:200]}")
            raise ValueError("No JSON in polish response")

        data = json.loads(match.group())
        logger.debug(f"Parsed polish data: {data}")
        return data
    except Exception as exc:
        logger.warning("Polish failed: %s", exc)
        # Return original story data
        return {
            "hero_name": story.hero_name,
            "hero_backstory": story.hero_backstory,
            "challenge": story.challenge,
            "resolution": story.resolution,
        }


MODERATION_SYSTEM = """You are a content moderator for a classroom creative writing exercise.
Check if the story is appropriate for students (PG-13 rating) and contains no copyrighted characters or settings.

Respond ONLY with valid JSON (no markdown):
{
  "approved": <boolean>,
  "reason": "<specific issue if rejected, empty string if approved>"
}

ONLY reject if:
- Contains EXPLICIT violence, gore, graphic death scenes, or detailed descriptions of injury
- Contains sexual content, graphic romantic scenes, or suggestive material
- Contains profanity or hate speech
- Uses copyrighted characters (Marvel, DC, Disney, Harry Potter, Star Wars, etc.)
- Uses trademarked settings or franchises

APPROVE stories that:
- Reference war, military service, or historical conflicts WITHOUT graphic violence
- Contain action, adventure, mystery, or suspense with age-appropriate tension
- Feature original characters in generic settings (fantasy, sci-fi, detective, historical fiction)
- Mention danger, peril, or conflict in non-graphic ways

Be permissive - only block truly inappropriate content, not mature themes handled tastefully."""


async def moderate_story(hero_name: str, hero_backstory: str, challenge: str, resolution: str, config: ModelConfig) -> tuple[bool, str]:
    """Check story content for appropriateness and copyright violations.

    Returns (approved: bool, reason: str) - reason is empty if approved.
    """
    prompt = f"""Story to check:
Hero: {hero_name} — {hero_backstory}
Challenge: {challenge}
Resolution: {resolution}

Is this appropriate for a classroom and free of copyrighted content?"""

    client = _get_client(config)
    try:
        resp = await client.chat.completions.create(
            model=config.model_name,
            messages=[
                {"role": "system", "content": MODERATION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=512,
        )
        raw = resp.choices[0].message.content or ""
        raw = raw.strip()
        logger.debug(f"Moderation response: {raw}")

        # Extract JSON
        match = re.search(r"\{[^}]+\}", raw)
        if not match:
            logger.warning(f"No JSON in moderation response: {raw}")
            # Fail open - allow if moderation fails
            return True, ""

        data = json.loads(match.group())
        approved = data.get("approved", True)
        reason = data.get("reason", "")

        logger.info(f"Moderation result: approved={approved}, reason={reason}")
        return approved, reason

    except Exception as exc:
        logger.error(f"Moderation check failed: {exc}")
        # Fail open - allow if moderation system fails
        return True, ""


# ══════════════════════════════════════════════════════════════════════════════
# INTRUSION DETECTION GRADING
# ══════════════════════════════════════════════════════════════════════════════

DETECTION_GRADING_SYSTEM = """You are a cybersecurity instructor grading network intrusion detection SCRIPTS.

Students wrote detection rules/prompts to identify intrusions in network traffic. Grade their detection script.

ACTUAL ATTACK: {attack_description}

GRADING CRITERIA:
1. Detection Accuracy (1-10): Does the script correctly identify the actual intrusion(s)? Would it catch the attack?
   - 1-3: Misses the attack entirely or targets wrong patterns
   - 4-6: Partially identifies attack but misses key indicators
   - 7-8: Correctly identifies attack with most key indicators
   - 9-10: Perfect detection logic covering all attack indicators

2. False Positive Elimination (1-10): Does the script avoid flagging legitimate traffic or red herrings?
   - 1-3: Would trigger many false alarms on normal traffic
   - 4-6: Some false positives from red herrings (e.g., isolated SSH failures)
   - 7-8: Good filtering, minimal false positives
   - 9-10: Precise filtering that eliminates all false positives

3. Efficiency (1-10): How efficient is the detection logic considering token count vs detection score?
   - 1-3: Verbose, redundant, or unnecessarily complex
   - 4-6: Adequate but could be more concise
   - 7-8: Concise and focused detection logic
   - 9-10: Minimal tokens, maximum detection precision

Use decimal precision (e.g., 5.6, 7.2, 8.4) to differentiate.

Respond ONLY with valid JSON (no markdown):
{{"accuracy": <float 1-10>, "specificity": <float 1-10>, "clarity": <float 1-10>}}"""


def _summarize_traffic(traffic: NetworkTraffic) -> str:
    """Create text summary of traffic highlighting patterns for AI grading context."""
    # Group by MAC address to show attack pattern
    mac_counts = {}
    port_targets = {}
    ip_mac_mapping = {}

    for entry in traffic.entries:
        # Count events per MAC
        mac_counts[entry.source_mac] = mac_counts.get(entry.source_mac, 0) + 1

        # Track IPs for each MAC
        if entry.source_mac not in ip_mac_mapping:
            ip_mac_mapping[entry.source_mac] = set()
        ip_mac_mapping[entry.source_mac].add(entry.source_ip)

        # Count target destinations
        key = f"{entry.dest_ip}:{entry.dest_port}"
        port_targets[key] = port_targets.get(key, 0) + 1

    # Format top patterns
    top_macs = sorted(mac_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    top_targets = sorted(port_targets.items(), key=lambda x: x[1], reverse=True)[:10]

    summary = f"Total events: {len(traffic.entries)}\n\n"
    summary += "Top source MACs (by event count):\n"
    for mac, count in top_macs:
        ips = ip_mac_mapping.get(mac, set())
        summary += f"  {mac}: {count} events, {len(ips)} different IPs\n"

    summary += "\nTop target destinations:\n"
    for target, count in top_targets:
        summary += f"  {target}: {count} events\n"

    return summary


async def grade_detection(
    prompt: DetectionPrompt,
    traffic: NetworkTraffic,
    rubric: Rubric,
    config: ModelConfig
) -> tuple[DetectionScore, Optional[str]]:
    """Grade a detection prompt against actual traffic data.

    Returns (score, error_message) where error_message is None on success.
    """
    traffic_summary = _summarize_traffic(traffic)

    grading_prompt = f"""Traffic Summary:
{traffic_summary}

Student Detection:
{prompt.detection_text}

Actual Attack:
{traffic.attack_description}

Grade on:
1. {rubric.criterion1} (Accuracy)
2. {rubric.criterion2} (Specificity)
3. {rubric.criterion3} (Clarity)"""

    client = _get_client(config)
    try:
        logger.debug(f"Starting detection grading for prompt {prompt.id}")
        resp = await client.chat.completions.create(
            model=config.model_name,
            messages=[
                {"role": "system", "content": DETECTION_GRADING_SYSTEM.format(
                    attack_description=traffic.attack_description
                )},
                {"role": "user", "content": grading_prompt},
            ],
            temperature=0.2,
            max_tokens=512,
        )
        raw = resp.choices[0].message.content or ""
        raw = raw.strip()
        logger.debug(f"Detection grading response: {raw[:100]}")

        # Extract JSON
        match = re.search(r"\{[^}]+\}", raw)
        if not match:
            raise ValueError(f"No JSON in response: {raw!r}")

        data = json.loads(match.group())
        score = DetectionScore(
            accuracy=float(data.get("accuracy", 5)),
            specificity=float(data.get("specificity", 5)),
            clarity=float(data.get("clarity", 5)),
        )
        logger.debug(f"Detection {prompt.id} graded: {score.composite}")
        return score, None

    except Exception as exc:
        error_msg = f"Detection grading failed ({config.provider}/{config.model_name}): {str(exc)}"
        logger.warning("Grading failed for detection %s: %s", prompt.id, exc)
        return DetectionScore(), error_msg


async def grade_all_detections(
    prompts: dict[str, DetectionPrompt],
    traffic: NetworkTraffic,
    rubric: Rubric,
    config: ModelConfig
) -> tuple[dict[str, DetectionScore], list[str]]:
    """Grade all detection prompts with rate limiting.

    Returns (scores, errors) where:
    - scores: mapping prompt_id → DetectionScore
    - errors: list of error messages from failed gradings
    """
    import asyncio

    scores = {}
    errors = []

    # Process in batches of 3 with 2s delays between batches
    prompt_ids = list(prompts.keys())
    batch_size = 3

    for i in range(0, len(prompt_ids), batch_size):
        batch = prompt_ids[i:i+batch_size]
        logger.info(f"Grading detection batch {i//batch_size + 1}/{(len(prompt_ids)-1)//batch_size + 1}")

        tasks = [grade_detection(prompts[pid], traffic, rubric, config) for pid in batch]
        results = await asyncio.gather(*tasks)

        for pid, (score, error) in zip(batch, results):
            scores[pid] = score
            if error:
                errors.append(error)

        # Rate limiting delay between batches
        if i + batch_size < len(prompt_ids):
            await asyncio.sleep(2)

    return scores, errors


SURPRISE_DETECTION_SYSTEM = """You are a student writing a GENERIC network intrusion detection SCRIPT/RULE.

Write a MEDIOCRE detection script - good enough to show some understanding but missing key details.

CRITICAL RULES:
1. Be GENERIC - use pattern descriptions NOT specific values (MAC addresses, IPs, exact port numbers)
2. Score around 5-6/10 quality - include SOME correct logic but miss important details
3. Write as a detection rule/script, NOT prose description
4. Use generic patterns like "IP masquerading", "MAC correlation", "port scanning"

GOOD examples (generic patterns):
- "ALERT if same source MAC with multiple different source IPs probing various important ports"
- "Flag when single hardware address targets multiple services within short time window"
- "Detect MAC address repeatedly connecting to different high-value ports, exclude single SSH failures"

BAD examples (too specific to avoid):
- "ALERT if MAC 00:1a:2b:3c:4d:5e..." (DON'T use actual MAC addresses)
- "Flag ports 22, 80, 443, 3389..." (DON'T list specific port numbers)
- "Exactly 20 events in 15 minutes" (DON'T use exact thresholds from current traffic)

Your detection should be mediocre by:
- Missing one key correlation (time window OR false positive filter OR threshold)
- Being somewhat vague on exact thresholds
- Partial logic that would work on similar attacks, not just this specific one

Respond ONLY with valid JSON (no markdown):
{{"detection_text": "<your generic mediocre detection script>"}}"""


async def surprise_me_detection(
    traffic: NetworkTraffic,
    config: ModelConfig
) -> dict:
    """Generate a mediocre AI detection to keep competition fair.

    Returns dict with "detection_text" field containing a 5-6/10 quality detection.
    """
    # Don't give specific traffic details - just general attack type
    attack_type = traffic.metadata.get('attack_type', 'port_scan')

    prompt = f"""You are analyzing network traffic that contains a {attack_type} attack.

Write a GENERIC detection SCRIPT/RULE that identifies SOME aspects of the intrusion but is not perfect.

REMEMBER: Be GENERIC - describe patterns, not specific values!
- DON'T mention specific MAC addresses, IPs, or exact port numbers
- DO describe patterns like "IP masquerading", "MAC correlation", "multiple port probes"
- Think about what would work on SIMILAR attacks, not just this exact traffic

Be a B/C student - include partial logic but miss important details:
- Maybe identify the general attack pattern but use vague thresholds
- Or notice one indicator (like multiple ports) but miss another (like time window)
- Perhaps forget to filter out a common false positive

Write your mediocre-but-generic detection script (not a prose description)."""

    client = _get_client(config)
    try:
        resp = await client.chat.completions.create(
            model=config.model_name,
            messages=[
                {"role": "system", "content": SURPRISE_DETECTION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.8,
            max_tokens=512,
        )
        raw = resp.choices[0].message.content or ""
        raw = raw.strip()
        match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if not match:
            raise ValueError("No JSON in surprise detection response")
        return json.loads(match.group())
    except Exception as exc:
        logger.warning("Surprise detection failed: %s", exc)
        # Fallback mediocre generic detection
        return {
            "detection_text": (
                "ALERT if multiple connection attempts to various services from rotating source IPs\n"
                "Look for patterns where different IPs target the same destination\n"
                "Flag activity that might indicate port scanning or network reconnaissance\n"
                "NOTE: May need refinement for time window and false positive filtering"
            )
        }


POLISH_DETECTION_SYSTEM = """You are a cybersecurity writing coach.

Improve this detection report based on grading feedback and traffic evidence.
Make it more accurate, specific, and clear.

Respond ONLY with valid JSON (no markdown):
{{"detection_text": "<improved version>"}}"""


async def polish_detection(
    prompt: DetectionPrompt,
    traffic: NetworkTraffic,
    rubric: Rubric,
    score: DetectionScore,
    config: ModelConfig
) -> dict:
    """Rewrite detection to improve score based on feedback.

    Returns dict with "detection_text" field containing improved detection.
    """
    feedback = []
    if score.accuracy < 8:
        feedback.append(f"Improve {rubric.criterion1} (current score: {score.accuracy}/10)")
    if score.specificity < 8:
        feedback.append(f"Improve {rubric.criterion2} (current score: {score.specificity}/10)")
    if score.clarity < 8:
        feedback.append(f"Improve {rubric.criterion3} (current score: {score.clarity}/10)")

    if not feedback:
        feedback.append("Already strong - refine wording for maximum clarity")

    traffic_summary = _summarize_traffic(traffic)

    polish_prompt = f"""Current Detection:
{prompt.detection_text}

Traffic Summary:
{traffic_summary}

Actual Attack Pattern:
{traffic.attack_description}

Areas to improve:
{chr(10).join('- ' + f for f in feedback)}

Rewrite this detection to be more accurate, specific, and clear based on the traffic evidence."""

    client = _get_client(config)
    try:
        resp = await client.chat.completions.create(
            model=config.model_name,
            messages=[
                {"role": "system", "content": POLISH_DETECTION_SYSTEM},
                {"role": "user", "content": polish_prompt},
            ],
            temperature=0.7,
            max_tokens=512,
        )
        raw = resp.choices[0].message.content or ""
        raw = raw.strip()
        match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if not match:
            raise ValueError("No JSON in polish response")
        return json.loads(match.group())
    except Exception as exc:
        logger.warning("Polish detection failed: %s", exc)
        # Return original on failure
        return {"detection_text": prompt.detection_text}
