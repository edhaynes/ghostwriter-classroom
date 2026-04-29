"""Data models for Ghostwriter Classroom sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class Phase(str, Enum):
    SETUP = "setup"
    WRITING = "writing"
    REVIEW = "review"
    FINAL_VOTE = "final_vote"
    REVEAL = "reveal"


class SessionMode(str, Enum):
    """Mode determines the type of activity (storytelling or intrusion detection)."""
    STORYTELLING = "storytelling"
    INTRUSION_DETECTION = "intrusion_detection"


class StoryArc(BaseModel):
    step1: str = ""   # Introduce the hero
    step2: str = ""   # Hero faces a challenge
    step3: str = ""   # Hero overcomes it


class Rubric(BaseModel):
    criterion1: str = "How likeable is the hero?"
    criterion2: str = "How difficult was the challenge?"
    criterion3: str = "How satisfying was the resolution?"


class AIScore(BaseModel):
    criterion1: float = 0.0   # 1–10
    criterion2: float = 0.0
    criterion3: float = 0.0

    @property
    def composite(self) -> float:
        return round((self.criterion1 + self.criterion2 + self.criterion3) / 3, 2)


class Story(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    author_username: str
    hero_name: str
    hero_backstory: str
    challenge: str
    resolution: str
    surprise_me: bool = False   # True if AI-generated via dice roll

    # Scoring (populated during review / reveal)
    peer_votes: int = 0
    final_votes: int = 0
    ai_score_review: Optional[AIScore] = None   # Graded at end of review phase
    ai_score_reveal: Optional[AIScore] = None   # Final re-grade at reveal

    @property
    def promotion_score(self) -> float:
        """Combined score used to pick the top 2 finalists."""
        if not self.ai_score_review:
            return float(self.peer_votes)
        return self.peer_votes * 0.6 + self.ai_score_review.composite * 0.4


class NetworkTrafficEntry(BaseModel):
    """Single network event for intrusion detection mode."""
    timestamp: datetime
    source_ip: str
    source_mac: str
    dest_ip: str
    dest_port: int
    protocol: str
    event_type: str  # "connection", "failed_login", "port_scan", etc.
    details: Optional[str] = None


class NetworkTraffic(BaseModel):
    """Complete traffic dataset for intrusion detection session."""
    entries: list[NetworkTrafficEntry]
    attack_description: str  # Ground truth for grading
    metadata: dict[str, Any] = {}


class DetectionPrompt(BaseModel):
    """Student's detection submission for intrusion detection mode."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    author_username: str
    detection_text: str
    ai_generated: bool = False  # True if from "surprise me"

    # Scoring (populated during review / reveal)
    ai_score_review: Optional['DetectionScore'] = None
    ai_score_reveal: Optional['DetectionScore'] = None


class DetectionScore(BaseModel):
    """AI grading for detection prompts (3 criteria like AIScore)."""
    accuracy: float = 0.0      # 1-10
    specificity: float = 0.0   # 1-10
    clarity: float = 0.0       # 1-10

    @property
    def composite(self) -> float:
        return round((self.accuracy + self.specificity + self.clarity) / 3, 2)


class Student(BaseModel):
    username: str
    session_id: str
    has_submitted: bool = False
    review_votes_cast: int = 0       # Track how many peer votes done (max 2)
    final_vote_cast: Optional[str] = None   # story_id


class PeerVote(BaseModel):
    voter_username: str
    story_a_id: str
    story_b_id: str
    winner_id: str


class ModelConfig(BaseModel):
    """AI model configuration for story generation and grading."""
    provider: str = "groq"  # "groq", "kserve", or "ollama"
    endpoint: str = "https://api.groq.com/openai/v1"
    model_name: str = "llama-3.3-70b-versatile"
    api_key: str = ""  # Only needed for Groq


class PolishResult(BaseModel):
    """Result of AI polish process."""
    original_story: Story
    polished_story: Story
    original_score: AIScore
    polished_score: AIScore
    rounds: int


class Session(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:3].upper())
    mode: SessionMode = SessionMode.STORYTELLING  # Session mode (storytelling or detection)
    phase: Phase = Phase.SETUP
    story_arc: StoryArc = Field(default_factory=StoryArc)
    rubric: Rubric = Field(default_factory=Rubric)
    llm_config: ModelConfig = Field(default_factory=ModelConfig)  # For story generation
    grading_config: Optional[ModelConfig] = None  # For grading (independent model)
    writing_seconds: int = 300  # Duration of writing phase (default 5 minutes)
    students: dict[str, Student] = {}    # username → Student

    # Storytelling mode fields
    stories: dict[str, Story] = {}       # story_id → Story

    # Intrusion detection mode fields
    detection_prompts: dict[str, DetectionPrompt] = {}  # prompt_id → DetectionPrompt
    network_traffic: Optional[NetworkTraffic] = None

    # Common fields
    finalists: list[str] = []            # story_ids or prompt_ids of top 2
    winner_id: Optional[str] = None
    ai_winner_id: Optional[str] = None
    writing_deadline: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    polish_result: Optional[PolishResult] = None  # Result of AI polish on winner
    ai_errors: list[str] = []  # Track AI operation failures to surface to instructor

    @model_validator(mode='after')
    def set_mode_aware_defaults(self):
        """Set rubric defaults based on session mode if using default rubric."""
        # Only override if rubric still has default storytelling values
        default_rubric = Rubric()
        if (self.rubric.criterion1 == default_rubric.criterion1 and
            self.rubric.criterion2 == default_rubric.criterion2 and
            self.rubric.criterion3 == default_rubric.criterion3):

            if self.mode == SessionMode.INTRUSION_DETECTION:
                self.rubric = Rubric(
                    criterion1="How accurate is the detection? (Does it identify the actual attack?)",
                    criterion2="How specific is the detection? (Does it avoid false positives?)",
                    criterion3="How clear and efficient is the detection logic?"
                )
        return self
