"""Phase 5 Intelligence Evidence Graph.

Durable lineage contract for the intelligence plane. The graph records why an
ActionIntent exists without granting the intelligence layer execution authority.

Canonical lineage:
    Signal -> Interpretation -> Decision -> Recommendation -> ActionIntent

This module is intentionally storage-agnostic. It validates graph records and
produces deterministic fingerprints suitable for a durable repository/outbox.
It never imports or invokes the Action Kernel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping
from uuid import uuid4

SCHEMA_VERSION = "KDO.IntelligenceEvidenceGraph/v1"
NODE_TYPES = {"signal", "interpretation", "decision", "recommendation", "action_intent"}
EDGE_TYPES = {
    ("signal", "interpretation"),
    ("interpretation", "decision"),
    ("decision", "recommendation"),
    ("recommendation", "action_intent"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceNode:
    node_id: str
    node_type: str
    workspace_id: str
    payload: Mapping[str, Any]
    provenance: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    schema_version: str = SCHEMA_VERSION
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.node_id.strip():
            raise ValueError("node_id is required")
        if self.node_type not in NODE_TYPES:
            raise ValueError(f"unsupported node_type: {self.node_type}")
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not isinstance(self.payload, Mapping):
            raise ValueError("payload must be a mapping")
        if not isinstance(self.provenance, Mapping):
            raise ValueError("provenance must be a mapping")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported evidence graph schema")

    def to_dict(self) -> dict[str, Any]:
        value = {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "workspace_id": self.workspace_id,
            "payload": dict(self.payload),
            "provenance": dict(self.provenance),
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }
        value["fingerprint"] = _fingerprint(value)
        return value


@dataclass(frozen=True)
class EvidenceEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    source_type: str
    target_type: str
    workspace_id: str
    relation: str = "supports"
    created_at: str = field(default_factory=_now)
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if not self.edge_id.strip() or not self.source_node_id.strip() or not self.target_node_id.strip():
            raise ValueError("edge and node ids are required")
        if self.source_node_id == self.target_node_id:
            raise ValueError("evidence graph cannot contain self edges")
        if (self.source_type, self.target_type) not in EDGE_TYPES:
            raise ValueError(f"invalid evidence lineage: {self.source_type}->{self.target_type}")
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported evidence graph schema")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "edge_id": self.edge_id,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "source_type": self.source_type,
            "target_type": self.target_type,
            "workspace_id": self.workspace_id,
            "relation": self.relation,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }


class IntelligenceEvidenceGraph:
    """Append-only graph validator/materializer for one or more workspaces."""

    def __init__(self) -> None:
        self._nodes: dict[str, EvidenceNode] = {}
        self._edges: dict[str, EvidenceEdge] = {}

    def add_node(
        self,
        *,
        node_type: str,
        workspace_id: str,
        payload: Mapping[str, Any],
        provenance: Mapping[str, Any] | None = None,
        node_id: str | None = None,
        created_at: str | None = None,
    ) -> EvidenceNode:
        node = EvidenceNode(
            node_id=node_id or f"ieg_{uuid4().hex}",
            node_type=node_type,
            workspace_id=workspace_id,
            payload=dict(payload),
            provenance=dict(provenance or {}),
            **({"created_at": created_at} if created_at is not None else {}),
        )
        if node.node_id in self._nodes:
            raise ValueError("node_id already exists")
        self._validate_node_provenance(node)
        self._nodes[node.node_id] = node
        return node

    def link(self, source_node_id: str, target_node_id: str, *, relation: str = "supports") -> EvidenceEdge:
        source = self._require_node(source_node_id)
        target = self._require_node(target_node_id)
        if source.workspace_id != target.workspace_id:
            raise ValueError("cross-workspace evidence edges are forbidden")
        edge = EvidenceEdge(
            edge_id=f"ieg_e_{uuid4().hex}",
            source_node_id=source.node_id,
            target_node_id=target.node_id,
            source_type=source.node_type,
            target_type=target.node_type,
            workspace_id=source.workspace_id,
            relation=relation,
        )
        edge.validate()
        self._edges[edge.edge_id] = edge
        return edge

    def add_action_intent(
        self,
        *,
        workspace_id: str,
        action_intent: Mapping[str, Any],
        recommendation_node_id: str,
        provenance: Mapping[str, Any] | None = None,
    ) -> EvidenceNode:
        recommendation = self._require_node(recommendation_node_id)
        if recommendation.node_type != "recommendation":
            raise ValueError("ActionIntent must descend from a recommendation node")
        if recommendation.workspace_id != workspace_id:
            raise ValueError("ActionIntent workspace does not match recommendation")
        required = ("action_type", "idempotency_key")
        missing = [key for key in required if not action_intent.get(key)]
        if missing:
            raise ValueError(f"ActionIntent missing required fields: {missing}")
        node = self.add_node(
            node_type="action_intent",
            workspace_id=workspace_id,
            payload=dict(action_intent),
            provenance={**dict(provenance or {}), "recommendation_node_id": recommendation_node_id},
        )
        self.link(recommendation_node_id, node.node_id, relation="generated")
        return node

    def get_node(self, node_id: str) -> EvidenceNode:
        return self._require_node(node_id)

    def validate_lineage(self, action_intent_node_id: str) -> dict[str, Any]:
        action = self._require_node(action_intent_node_id)
        if action.node_type != "action_intent":
            raise ValueError("lineage root must be an action_intent")
        chain: list[EvidenceNode] = [action]
        current = action
        expected = {
            "action_intent": "recommendation",
            "recommendation": "decision",
            "decision": "interpretation",
            "interpretation": "signal",
        }
        while current.node_type != "signal":
            parent_edges = [
                edge for edge in self._edges.values()
                if edge.target_node_id == current.node_id
            ]
            candidates = [edge for edge in parent_edges if edge.source_type == expected[current.node_type]]
            if len(candidates) != 1:
                raise ValueError(
                    f"lineage for {current.node_id} requires exactly one parent of type "
                    f"{expected[current.node_type]}"
                )
            current = self._require_node(candidates[0].source_node_id)
            if current.workspace_id != action.workspace_id:
                raise ValueError("lineage crosses workspace boundary")
            chain.append(current)
        chain.reverse()
        return {
            "schema_version": SCHEMA_VERSION,
            "workspace_id": action.workspace_id,
            "action_intent_node_id": action.node_id,
            "lineage": [node.to_dict() for node in chain],
            "edges": [
                edge.to_dict()
                for edge in self._edges.values()
                if edge.source_node_id in {n.node_id for n in chain}
                and edge.target_node_id in {n.node_id for n in chain}
            ],
            "complete": True,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "nodes": [node.to_dict() for node in self._nodes.values()],
            "edges": [edge.to_dict() for edge in self._edges.values()],
        }

    def _require_node(self, node_id: str) -> EvidenceNode:
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            raise ValueError(f"unknown evidence node: {node_id}") from exc

    @staticmethod
    def _validate_node_provenance(node: EvidenceNode) -> None:
        if node.node_type == "signal":
            if not node.provenance.get("source"):
                raise ValueError("signal provenance.source is required")
        elif node.node_type == "interpretation":
            if not node.provenance.get("signal_node_id"):
                raise ValueError("interpretation provenance.signal_node_id is required")
        elif node.node_type == "decision":
            if not node.provenance.get("interpretation_node_id"):
                raise ValueError("decision provenance.interpretation_node_id is required")
        elif node.node_type == "recommendation":
            if not node.provenance.get("decision_node_id"):
                raise ValueError("recommendation provenance.decision_node_id is required")
        elif node.node_type == "action_intent":
            if not node.provenance.get("recommendation_node_id"):
                raise ValueError("action_intent provenance.recommendation_node_id is required")


__all__ = ["EvidenceNode", "EvidenceEdge", "IntelligenceEvidenceGraph", "SCHEMA_VERSION"]
