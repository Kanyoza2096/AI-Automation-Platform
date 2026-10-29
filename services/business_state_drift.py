"""Phase 3 — Business State & Drift Engine projection.

This service composes existing KDO state/reconciliation primitives into a
workspace-scoped, deterministic business drift read model. It never executes,
authorizes, mutates desired state, or performs remediation.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from models.business_state_drift import (
    SCHEMA,
    BusinessStateDriftFinding,
    BusinessStateDriftSnapshot,
    BusinessStateResource,
)
from services.phase9_reconciliation import AuthoritativeObservation, ExpectedState, reconcile


class BusinessStateDriftProjector:
    """Project desired and authoritative observed state into business drift."""

    def project(
        self,
        *,
        workspace_id: str,
        desired_states: Iterable[ExpectedState] = (),
        observations: Iterable[AuthoritativeObservation] = (),
        graph: Any | None = None,
    ) -> BusinessStateDriftSnapshot:
        if not str(workspace_id).strip():
            raise ValueError("workspace_id is required")

        expected_by_ref: dict[str, ExpectedState] = {}
        observations_by_ref: dict[str, AuthoritativeObservation] = {}
        warnings: list[str] = []

        for expected in desired_states:
            if expected.workspace_id != workspace_id:
                warnings.append(f"desired_workspace_mismatch:{expected.resource_ref}")
                continue
            previous = expected_by_ref.get(expected.resource_ref)
            if previous is not None and previous.state != expected.state:
                warnings.append(f"duplicate_desired_state:{expected.resource_ref}")
                continue
            expected_by_ref[expected.resource_ref] = expected

        for observation in observations:
            if observation.workspace_id != workspace_id:
                warnings.append(f"observation_workspace_mismatch:{observation.resource_ref}")
                continue
            previous = observations_by_ref.get(observation.resource_ref)
            if previous is None or observation.observed_at > previous.observed_at:
                observations_by_ref[observation.resource_ref] = observation

        graph_nodes: dict[str, Any] = {}
        if graph is not None:
            if getattr(graph, "workspace_id", workspace_id) != workspace_id:
                warnings.append("graph_workspace_mismatch")
            else:
                graph_nodes = {str(n.node_id): n for n in getattr(graph, "nodes", ())}

        findings: list[BusinessStateDriftFinding] = []
        for resource_ref in sorted(expected_by_ref):
            expected = expected_by_ref[resource_ref]
            observation = observations_by_ref.get(resource_ref)
            finding = reconcile(expected, observation)
            node_id, node_type = self._resolve_graph_node(resource_ref, graph_nodes)
            resource = BusinessStateResource(
                workspace_id=workspace_id,
                resource_ref=resource_ref,
                node_id=node_id,
                node_type=node_type,
                desired_state=dict(expected.state),
                observed_state=dict(observation.state) if observation is not None else None,
                evidence_refs=finding.evidence_refs,
            )
            findings.append(BusinessStateDriftFinding(resource=resource, finding=finding))

        coverage = {
            "desired_resources": len(expected_by_ref),
            "observations": len(observations_by_ref),
            "matched": sum(f.health.value == "matched" for f in findings),
            "drifted": sum(f.health.value == "drifted" for f in findings),
            "unknown": sum(f.health.value == "unknown" for f in findings),
            "observation_unavailable": sum(f.health.value == "observation_unavailable" for f in findings),
            "graph_nodes": len(graph_nodes),
            "findings": len(findings),
        }
        if not expected_by_ref:
            warnings.append("no_desired_state")
        if expected_by_ref and not observations_by_ref:
            warnings.append("no_authoritative_observations")

        return BusinessStateDriftSnapshot(
            schema=SCHEMA,
            workspace_id=workspace_id,
            findings=tuple(findings),
            coverage=coverage,
            warnings=tuple(sorted(set(warnings))),
        )

    @staticmethod
    def _resolve_graph_node(resource_ref: str, nodes: Mapping[str, Any]) -> tuple[str | None, str | None]:
        # Resource refs may deliberately be external provider identifiers. Only
        # explicit node-id equality is resolved; names are never guessed.
        node = nodes.get(resource_ref)
        if node is None:
            return None, None
        node_type = getattr(getattr(node, "node_type", None), "value", getattr(node, "node_type", None))
        return str(node.node_id), str(node_type) if node_type is not None else None


__all__ = ["BusinessStateDriftProjector"]
