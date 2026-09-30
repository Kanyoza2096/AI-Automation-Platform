"""V72.2 — Command Center backend read model and truth adapters.

Read-only aggregation over existing authoritative KDO planes. This module never
creates action authority and never executes providers. Missing upstream truth is
reported as UNAVAILABLE/UNKNOWN rather than fabricated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
import hashlib
import json


SECTIONS = (
    "businessHealth", "objectives", "opportunities", "risks", "decisions",
    "workforce", "operations", "outcomes", "evidence", "governance",
)


@dataclass(frozen=True)
class TruthValue:
    section: str
    state: str
    data_class: str
    authority_source: str
    freshness: str
    confidence: str
    evidence: tuple[Mapping[str, Any], ...]
    data: Mapping[str, Any]
    actions_allowed: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "state": self.state,
            "dataClass": self.data_class,
            "authoritySource": self.authority_source,
            "freshness": self.freshness,
            "confidence": self.confidence,
            "evidence": [dict(x) for x in self.evidence],
            "data": dict(self.data),
            "actionsAllowed": list(self.actions_allowed),
            "actionAuthorityGranted": False,
        }


class CommandCenterReadModel:
    """Builds a workspace-scoped, evidence-carrying read model.

    The adapters accept already-authoritative snapshots. They do not infer
    business facts from UI state and they never call Action Kernel/provider code.
    """

    VERSION = "v72.4"

    def __init__(self, workspace_id: str):
        if not str(workspace_id).strip():
            raise ValueError("workspace_id is required")
        self.workspace_id = str(workspace_id)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _fingerprint(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

    def _value(self, section: str, source: str, data: Mapping[str, Any] | None,
               *, state: str = "LIVE", data_class: str = "OBSERVED",
               confidence: str = "HIGH", evidence: tuple[Mapping[str, Any], ...] = ()) -> TruthValue:
        if data is None:
            return TruthValue(section, "UNKNOWN", "UNAVAILABLE", source, "unknown", "NONE", evidence, {})
        return TruthValue(section, state, data_class, source, self._now(), confidence, evidence, data)

    def build(self, sources: Mapping[str, Any]) -> dict[str, Any]:
        """Build canonical read model from named authoritative source snapshots."""
        sections: dict[str, TruthValue] = {}
        mappings = {
            "businessHealth": ("observed_runtime", "systemHealth"),
            "objectives": ("business_operating_layer", "objectives"),
            "opportunities": ("business_intelligence", "opportunities"),
            "risks": ("governance_and_observed_runtime", "risks"),
            "decisions": ("governance", "decisions"),
            "workforce": ("governed_workforce", "workforce"),
            "operations": ("business_operating_layer", "operations"),
            "outcomes": ("outcome_measurement", "outcomes"),
            "evidence": ("evidence_plane", "evidence"),
            "governance": ("governance", "governance"),
        }
        for section, (authority, key) in mappings.items():
            raw = sources.get(key)
            evidence = ()
            if raw is not None:
                evidence = ({"source": key, "fingerprint": self._fingerprint(raw)},)
            sections[section] = self._value(section, authority, raw, evidence=evidence)

        live = sum(1 for x in sections.values() if x.state == "LIVE")
        unknown = sum(1 for x in sections.values() if x.state == "UNKNOWN")
        state = "LIVE" if live == len(SECTIONS) else ("DEGRADED" if live else "UNKNOWN")
        return {
            "schema": "CommandCenterReadModel/v1",
            "version": self.VERSION,
            "generatedAt": self._now(),
            "workspaceId": self.workspace_id,
            "state": state,
            "summary": {"sectionCount": len(SECTIONS), "live": live, "unknown": unknown},
            "sections": {k: v.as_dict() for k, v in sections.items()},
            "authority": {
                "readOnly": True,
                "actionAuthorityGranted": False,
                "consequentialCommandsMustUse": [
                    "KCPG_GATEWAY", "GOVERNANCE", "ACTION_KERNEL", "DURABLE_OUTBOX",
                    "CONNECTOR_PROVIDER", "RECONCILIATION",
                ],
            },
        }

    def empty(self) -> dict[str, Any]:
        return self.build({})


def build_command_center_read_model(workspace_id: str, sources: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return CommandCenterReadModel(workspace_id).build(sources or {})
