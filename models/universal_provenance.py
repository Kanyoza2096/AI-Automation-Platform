"""Phase 7 — universal business provenance contracts.

This module is a read-model contract over existing evidence, audit, decision,
execution, verification, and outcome identities. It does not become a second
ledger or telemetry store and cannot grant execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

SCHEMA = "KDO.UniversalProvenance/v1"

NODE_TYPES = frozenset({
    "business_fact", "observation", "state", "drift", "decision", "simulation",
    "agent", "governance", "action_intent", "execution", "receipt", "verification",
    "outcome", "learning", "evidence", "signal", "recommendation",
})


@dataclass(frozen=True)
class ProvenanceNode:
    node_id: str
    node_type: str
    workspace_id: str
    source_id: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    correlation_id: str | None = None
    trace_id: str | None = None
    causation_id: str | None = None
    authority: str = "none"

    def __post_init__(self) -> None:
        if not self.node_id.strip() or not self.source_id.strip() or not self.workspace_id.strip():
            raise ValueError("node_id, source_id and workspace_id are required")
        if self.node_type not in NODE_TYPES:
            raise ValueError(f"unsupported provenance node type: {self.node_type}")
        if self.authority != "none":
            raise ValueError("provenance nodes cannot grant authority")


@dataclass(frozen=True)
class ProvenanceEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    workspace_id: str
    relation: str
    authority: str = "none"

    def __post_init__(self) -> None:
        if not self.edge_id.strip() or not self.source_node_id.strip() or not self.target_node_id.strip():
            raise ValueError("edge and node ids are required")
        if self.source_node_id == self.target_node_id:
            raise ValueError("provenance graph cannot contain self edges")
        if not self.workspace_id.strip() or not self.relation.strip():
            raise ValueError("workspace_id and relation are required")
        if self.authority != "none":
            raise ValueError("provenance edges cannot grant authority")


@dataclass(frozen=True)
class UniversalProvenance:
    schema: str
    workspace_id: str
    nodes: tuple[ProvenanceNode, ...]
    edges: tuple[ProvenanceEdge, ...]
    warnings: tuple[str, ...] = ()
    coverage: Mapping[str, int] = field(default_factory=dict)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if self.schema != SCHEMA or not self.workspace_id.strip():
            raise ValueError("invalid universal provenance snapshot")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workspace_id": self.workspace_id,
            "nodes": [
                {
                    "node_id": n.node_id,
                    "node_type": n.node_type,
                    "workspace_id": n.workspace_id,
                    "source_id": n.source_id,
                    "attributes": dict(n.attributes),
                    "evidence_refs": list(n.evidence_refs),
                    "correlation_id": n.correlation_id,
                    "trace_id": n.trace_id,
                    "causation_id": n.causation_id,
                    "authority": n.authority,
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "edge_id": e.edge_id,
                    "source_node_id": e.source_node_id,
                    "target_node_id": e.target_node_id,
                    "workspace_id": e.workspace_id,
                    "relation": e.relation,
                    "authority": e.authority,
                }
                for e in self.edges
            ],
            "warnings": list(self.warnings),
            "coverage": dict(self.coverage),
            "fingerprint": self.fingerprint,
            "authority": {
                "read_only": True,
                "does_not_execute": True,
                "does_not_authorize": True,
                "does_not_mutate_source_records": True,
                "source_of_truth": False,
            },
        }
