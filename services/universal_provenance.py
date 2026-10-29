"""Phase 7 — universal provenance/observability read-model projector.

The projector converges existing KDO identities into one auditable lineage
view. It consumes records; it does not persist, mutate, authorize, execute,
or replace any existing ledger, evidence graph, telemetry, or reconciliation
system.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from models.universal_provenance import (
    SCHEMA, NODE_TYPES, ProvenanceEdge, ProvenanceNode, UniversalProvenance,
)


def _stable_id(prefix: str, *parts: str) -> str:
    material = "|".join(str(x) for x in parts)
    return f"kdo:{prefix}:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _fingerprint(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_KIND_ALIASES = {
    "fact": "business_fact", "business_knowledge": "business_fact",
    "observation": "observation", "metric": "observation", "trace": "observation",
    "state": "state", "drift": "drift", "decision": "decision",
    "scenario": "simulation", "simulation": "simulation", "agent": "agent",
    "governance": "governance", "action": "action_intent", "action_intent": "action_intent",
    "execution": "execution", "receipt": "receipt", "verification": "verification",
    "outcome": "outcome", "learning": "learning", "evidence": "evidence",
    "signal": "signal", "recommendation": "recommendation",
}


class UniversalProvenanceProjector:
    """Project explicit provenance references into one deterministic graph."""

    def project(self, *, workspace_id: str, records: Sequence[Mapping[str, Any]]) -> UniversalProvenance:
        workspace_id = str(workspace_id or "").strip()
        if not workspace_id:
            raise ValueError("workspace_id is required")

        nodes_by_key: dict[str, ProvenanceNode] = {}
        source_rows: dict[str, Mapping[str, Any]] = {}
        warnings: list[str] = []

        for row in records:
            row_workspace = str(row.get("workspace_id") or row.get("workspaceId") or "").strip()
            if row_workspace != workspace_id:
                continue
            source_id = str(row.get("id") or row.get("source_id") or row.get("sourceId") or "").strip()
            raw_kind = str(row.get("kind") or row.get("type") or row.get("node_type") or "").strip().lower()
            node_type = _KIND_ALIASES.get(raw_kind, raw_kind)
            if not source_id or node_type not in NODE_TYPES:
                warnings.append(f"ignored record with invalid identity/type: {source_id or '<missing>'}/{raw_kind or '<missing>'}")
                continue
            key = f"{node_type}:{source_id}"
            existing = nodes_by_key.get(key)
            fingerprint = _fingerprint(dict(row))
            if existing is not None:
                if source_rows[key].get("_fingerprint") != fingerprint:
                    raise ValueError(f"conflicting provenance payload for {key}")
                continue
            evidence = row.get("evidence_refs", row.get("evidenceRefs", ())) or ()
            if isinstance(evidence, str):
                evidence = (evidence,)
            attributes = row.get("attributes") or row.get("metadata") or {}
            node = ProvenanceNode(
                node_id=_stable_id("prov", workspace_id, node_type, source_id),
                node_type=node_type,
                workspace_id=workspace_id,
                source_id=source_id,
                attributes=dict(attributes) if isinstance(attributes, Mapping) else {},
                evidence_refs=tuple(sorted({str(x) for x in evidence if str(x).strip()})),
                correlation_id=str(row.get("correlation_id") or row.get("correlationId") or "") or None,
                trace_id=str(row.get("trace_id") or row.get("traceId") or "") or None,
                causation_id=str(row.get("causation_id") or row.get("causationId") or "") or None,
            )
            nodes_by_key[key] = node
            source_rows[key] = {**dict(row), "_fingerprint": fingerprint}

        edges: dict[str, ProvenanceEdge] = {}
        for key, row in source_rows.items():
            target = nodes_by_key[key]
            parent_ids = row.get("parent_ids", row.get("parentIds", ())) or ()
            if isinstance(parent_ids, str):
                parent_ids = (parent_ids,)
            # Only explicit references are accepted; names, kinds, and timestamps
            # are never used to infer causality.
            refs: list[tuple[str, str]] = []
            for parent in parent_ids:
                parent_s = str(parent).strip()
                if not parent_s:
                    continue
                matching = [k for k in nodes_by_key if k.endswith(f":{parent_s}")]
                if len(matching) != 1:
                    warnings.append(f"unresolved explicit parent {parent_s} for {target.source_id}")
                    continue
                refs.append((matching[0], "caused_by"))
            evidence_parents = row.get("evidence_parent_ids", row.get("evidenceParentIds", ())) or ()
            if isinstance(evidence_parents, str):
                evidence_parents = (evidence_parents,)
            for parent in evidence_parents:
                parent_s = str(parent).strip()
                matching = [k for k in nodes_by_key if k.endswith(f":{parent_s}")]
                if len(matching) == 1:
                    refs.append((matching[0], "supported_by"))
                elif parent_s:
                    warnings.append(f"unresolved evidence parent {parent_s} for {target.source_id}")
            for source_key, relation in refs:
                source = nodes_by_key[source_key]
                edge_id = _stable_id("prov-edge", workspace_id, source.node_id, target.node_id, relation)
                edges[edge_id] = ProvenanceEdge(
                    edge_id=edge_id,
                    source_node_id=source.node_id,
                    target_node_id=target.node_id,
                    workspace_id=workspace_id,
                    relation=relation,
                )

        nodes = tuple(sorted(nodes_by_key.values(), key=lambda n: (n.node_type, n.source_id)))
        edge_values = tuple(sorted(edges.values(), key=lambda e: e.edge_id))
        coverage = {kind: sum(1 for n in nodes if n.node_type == kind) for kind in sorted(NODE_TYPES)}
        payload = {
            "schema": SCHEMA,
            "workspace_id": workspace_id,
            "nodes": [{"type": n.node_type, "id": n.source_id, "evidence": list(n.evidence_refs)} for n in nodes],
            "edges": [{"source": e.source_node_id, "target": e.target_node_id, "relation": e.relation} for e in edge_values],
            "warnings": sorted(set(warnings)),
            "coverage": coverage,
        }
        return UniversalProvenance(
            schema=SCHEMA,
            workspace_id=workspace_id,
            nodes=nodes,
            edges=edge_values,
            warnings=tuple(sorted(set(warnings))),
            coverage=coverage,
            fingerprint=_fingerprint(payload),
        )
