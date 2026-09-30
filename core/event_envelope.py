"""
Canonical event envelope — facts, not commands disguised as events.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class EventEnvelope:
    event_type: str
    workspace_id: str
    payload: Dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    organism_id: Optional[str] = None
    actor_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    schema_version: str = "1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Preferred fact-style event types
POST_CREATED = "PostCreated"
PUBLISH_REQUESTED = "PublishRequested"
PUBLISH_SUCCEEDED = "PublishSucceeded"
PUBLISH_FAILED = "PublishFailed"
DECISION_CREATED = "DecisionCreated"
GUARDIAN_BLOCKED_ACTION = "GuardianBlockedAction"
