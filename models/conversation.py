"""
Conversation and Memory domain models.

MemoryProfile is the authoritative user state object — all memory services
read and write this model rather than raw dicts.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import Field

from models.base import KanyozaModel


class ConversationRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ConversationTurn(KanyozaModel):
    """A single message exchange within a conversation."""

    role: ConversationRole
    content: str = Field(..., min_length=1)
    timestamp: datetime = Field(default_factory=KanyozaModel.utcnow)
    token_count: Optional[int] = Field(default=None)
    correlation_id: Optional[str] = Field(default=None)


class Conversation(KanyozaModel):
    """
    A complete conversation thread for a single user session.

    Short-term: held in memory, bounded by max_turns.
    Long-term: summarized and persisted to Supabase.
    """

    user_id: str = Field(..., description="Platform user identifier (PSID, phone, etc.)")
    platform: str = Field(default="facebook", description="Originating platform")
    turns: List[ConversationTurn] = Field(default_factory=list)
    summary: Optional[str] = Field(default=None, description="AI-generated summary of past turns")
    started_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    correlation_id: Optional[str] = Field(default=None)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def last_user_message(self) -> Optional[str]:
        for turn in reversed(self.turns):
            if turn.role == ConversationRole.USER:
                return turn.content
        return None

    def with_turn(self, role: ConversationRole, content: str, **kwargs) -> "Conversation":
        """Return a new Conversation with the additional turn appended."""
        new_turn = ConversationTurn(role=role, content=content, **kwargs)
        return self.model_copy(
            update={
                "turns": self.turns + [new_turn],
                "updated_at": KanyozaModel.utcnow(),
            }
        )

    def truncated(self, max_turns: int) -> "Conversation":
        """Return a new Conversation with at most max_turns recent turns."""
        return self.model_copy(update={"turns": self.turns[-max_turns:]})


class UserProfile(KanyozaModel):
    """
    Static profile information extracted from user interactions.
    Persisted to Supabase and cached in memory.
    """

    user_id: str
    platform: str = "facebook"
    display_name: Optional[str] = None
    locale: Optional[str] = None
    timezone_offset: Optional[int] = None
    interests: List[str] = Field(default_factory=list)
    preferred_language: str = "en"
    first_seen: datetime = Field(default_factory=KanyozaModel.utcnow)
    last_seen: datetime = Field(default_factory=KanyozaModel.utcnow)
    metadata: Dict[str, str] = Field(default_factory=dict)


class MemoryProfile(KanyozaModel):
    """
    Complete memory state for a user — combines conversation history,
    user profile, analytics, and relationship scoring.

    This is the single object passed between all memory-aware services.
    """

    user_id: str
    platform: str = "facebook"

    # Conversation memory
    active_conversation: Optional[Conversation] = None
    conversation_summary: Optional[str] = None

    # Profile
    profile: Optional[UserProfile] = None

    # Relationship analytics
    message_count: int = Field(default=0, ge=0)
    relationship_score: float = Field(default=0.0, ge=0.0, le=100.0)
    sentiment_trend: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="-1.0=very negative, 0=neutral, 1.0=very positive",
    )

    # Extracted interests (semantic)
    interests: List[str] = Field(default_factory=list)
    topics_discussed: List[str] = Field(default_factory=list)

    # Cache / expiry
    cached_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    expires_at: Optional[datetime] = None

    @property
    def is_new_user(self) -> bool:
        return self.message_count == 0

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return KanyozaModel.utcnow() > self.expires_at

    def increment_messages(self) -> "MemoryProfile":
        return self.model_copy(update={"message_count": self.message_count + 1})
