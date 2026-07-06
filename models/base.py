"""
Base model for all Kanyoza v10 domain models.
Provides immutability, JSON serialization, and safe dict export.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from pydantic import BaseModel, ConfigDict


class KanyozaModel(BaseModel):
    """
    Immutable base for all domain models.

    - frozen=True  → instances are hashable and cannot be mutated after creation
    - populate_by_name → allow both alias and field name during construction
    - Use .model_copy(update={...}) to derive modified instances
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary (JSON-safe types)."""
        return self.model_dump(mode="json")

    @classmethod
    def utcnow(cls) -> datetime:
        """UTC-aware current timestamp helper."""
        return datetime.now(timezone.utc)
