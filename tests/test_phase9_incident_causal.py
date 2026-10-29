from datetime import timezone

import pytest

from services.phase9_incident_causal import (
    CausalEdge,
    CausalRelationType,
    IncidentGraph,
    IncidentRecord,
    IncidentSeverity,
    IncidentStatus,
    validate_incident_transition,
)


def incident(**overrides):
    data = dict(
        workspace_id="ws_1",
        title="Provider degradation",
        summary="Provider latency exceeded operational threshold.",
        severity=IncidentSeverity.HIGH,
        correlation_id="corr_123",
        primary_evidence_ref="event:evt_1",
    )
    data.update(overrides)
    return IncidentRecord(**data)


def edge(**overrides):
    data = dict(
        workspace_id="ws_1",
        source_ref="event:evt_1",
        target_ref="incident:inc_1",
        relation=CausalRelationType.TRIGGERED_BY,
        correlation_id="corr_123",
        evidence_ref="trace:tr_1",
    )
    data.update(overrides)
    return CausalEdge(**data)


def test_incident_requires_workspace_and_evidence_shape():
    with pytest.raises(ValueError):
        incident(workspace_id=" ")
    with pytest.raises(ValueError):
        incident(primary_evidence_ref=" ")


def test_incident_is_authority_free():
    with pytest.raises(ValueError):
        incident(authority="operator")


def test_incident_status_transitions_are_bounded():
    item = incident()
    item = item.transition(IncidentStatus.INVESTIGATING)
    item = item.transition(IncidentStatus.CONTAINED)
    item = item.transition(IncidentStatus.RECOVERING)
    item = item.transition(IncidentStatus.RESOLVED)
    item = item.transition(IncidentStatus.CLOSED)
    assert item.status == IncidentStatus.CLOSED
    assert not validate_incident_transition(
        IncidentStatus.CLOSED, IncidentStatus.OPEN
    )


def test_invalid_status_transition_is_rejected():
    with pytest.raises(ValueError):
        incident().transition(IncidentStatus.RECOVERING)


def test_causal_edge_requires_evidence_and_correlation():
    with pytest.raises(ValueError):
        edge(correlation_id="")
    with pytest.raises(ValueError):
        edge(evidence_ref="")


def test_causal_edge_cannot_self_reference():
    with pytest.raises(ValueError):
        edge(source_ref="node:x", target_ref="node:x")


def test_causal_edge_is_authority_free():
    with pytest.raises(ValueError):
        edge(authority="executor")


def test_graph_rejects_cross_workspace_incident():
    graph = IncidentGraph("ws_1")
    with pytest.raises(ValueError):
        graph.add_incident(incident(workspace_id="ws_2"))


def test_graph_rejects_cross_workspace_edge():
    graph = IncidentGraph("ws_1")
    with pytest.raises(ValueError):
        graph.add_edge(edge(workspace_id="ws_2"))


def test_graph_can_project_impact_without_executing():
    inc = incident(incident_id="inc_1")
    graph = IncidentGraph("ws_1").add_incident(inc)
    e1 = edge(
        edge_id="e1",
        source_ref="incident:inc_1",
        target_ref="execution:exec_1",
        relation=CausalRelationType.AFFECTS,
    )
    e2 = edge(
        edge_id="e2",
        source_ref="execution:exec_1",
        target_ref="reconciliation:rec_1",
        relation=CausalRelationType.BLOCKED_BY,
    )
    graph = graph.add_edge(e1).add_edge(e2)
    assert graph.impact_path("incident:inc_1") == (
        "execution:exec_1",
        "reconciliation:rec_1",
    )


def test_graph_requires_referenced_incident_to_exist():
    graph = IncidentGraph("ws_1").add_incident(incident(incident_id="inc_1"))
    with pytest.raises(ValueError):
        IncidentGraph(
            "ws_1",
            incidents=graph.incidents,
            edges=(
                edge(
                    source_ref="incident:missing",
                    target_ref="execution:exec_1",
                ),
            ),
        )


def test_correlation_does_not_create_causation_implicitly():
    a = incident(incident_id="inc_a", correlation_id="same")
    b = incident(incident_id="inc_b", correlation_id="same")
    graph = IncidentGraph("ws_1").add_incident(a).add_incident(b)
    assert graph.edges == ()


def test_serialization_preserves_evidence_and_scope():
    item = incident(execution_id="exec_7", dependency_id="dep_3")
    data = item.to_dict()
    assert data["workspace_id"] == "ws_1"
    assert data["primary_evidence_ref"] == "event:evt_1"
    assert data["execution_id"] == "exec_7"
    assert data["dependency_id"] == "dep_3"


def test_edge_serialization_preserves_relationship():
    item = edge(relation=CausalRelationType.RECOVERED_BY)
    data = item.to_dict()
    assert data["relation"] == "RECOVERED_BY"
    assert data["evidence_ref"] == "trace:tr_1"
