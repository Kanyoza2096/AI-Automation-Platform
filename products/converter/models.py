# products/converter/models.py
"""
Pydantic models for the currency converter.
Strongly typed, immutable, self-validating.
"""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class ConversionRequest(BaseModel):
    """Incoming conversion request."""
    from_currency: str = Field(..., alias="from", min_length=3, max_length=3)
    to_currency: str = Field(..., alias="to", min_length=3, max_length=3)
    amount: float = Field(..., gt=0, le=999999999.99)

    @field_validator("from_currency", "to_currency")
    @classmethod
    def uppercase_currency(cls, v: str) -> str:
        return v.upper()

    model_config = {"populate_by_name": True}


class ConversionResult(BaseModel):
    """Successful conversion result."""
    from_currency: str
    to_currency: str
    amount: float
    result: float
    rate: float
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    model_config = {"frozen": True}


class RateCache(BaseModel):
    """Cached exchange rate."""
    base_currency: str
    rates: dict[str, float]
    cached_at: float  # Unix timestamp

    def is_expired(self, ttl_seconds: int = 3600) -> bool:
        """Check if cache is older than TTL."""
        import time
        return (time.time() - self.cached_at) > ttl_seconds


class UsageRecord(BaseModel):
    """API usage tracking."""
    api_key_hash: str
    endpoint: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    success: bool = True

    model_config = {"frozen": True}
