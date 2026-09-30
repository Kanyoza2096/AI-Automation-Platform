"""
Notification domain model — typed outbound alerts for any channel.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import Field

from models.base import KanyozaModel


class NotificationChannel(str, Enum):
    MESSENGER = "messenger"
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    WEBHOOK = "webhook"
    LOG = "log"


class NotificationPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class Notification(KanyozaModel):
    """
    A typed notification to be delivered over a specific channel.

    Created by any service that needs to alert an operator or user.
    Delivered by the NotificationService — callers never interact
    with the transport directly.
    """

    recipient_id: str = Field(..., description="Platform user/channel identifier")
    channel: NotificationChannel
    subject: Optional[str] = Field(default=None, description="Subject line (email/webhook)")
    body: str = Field(..., min_length=1, description="Main notification text")
    priority: NotificationPriority = Field(default=NotificationPriority.NORMAL)

    # Optional structured payload for rich notifications
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Delivery tracking
    delivered: bool = Field(default=False)
    delivered_at: Optional[datetime] = None
    error: Optional[str] = None

    # Tracing
    correlation_id: Optional[str] = None
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)

    def mark_delivered(self) -> "Notification":
        return self.model_copy(
            update={"delivered": True, "delivered_at": KanyozaModel.utcnow()}
        )

    def mark_failed(self, error: str) -> "Notification":
        return self.model_copy(update={"delivered": False, "error": error})
