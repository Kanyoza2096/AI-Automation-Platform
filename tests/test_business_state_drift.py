import pytest

from models.business_state_drift import BusinessStateHealth
from services.business_state_drift import BusinessStateDriftProjector
from services.phase9_reconciliation import AuthoritativeObservation, ExpectedState


def expected(resource="asset:42", state=None, workspace="ws_1"):
    return ExpectedState(
        workspace_id=workspace,
        resource_ref=resource,
        state=state or {"status": "active", "owner": "ops"},
        source_evidence_ref="desired:42",
    )


def observed(resource="asset:42", state=None, workspace="ws_1", observed_at="2026-10-07T08:00:00Z", evidence="obs:42"):
    return AuthoritativeObservation(
        workspace_id=workspace,
        resource_ref=resource,
        state=state or {"status": "active", "owner": "ops"},
        observed_at=observed_at,
        evidence_ref=evidence,
    )


def test_matching_business_state_is_projected():
    snapshot = BusinessStateDriftProjector().project(
        workspace_id="ws_1", desired_states=[expected()], observations=[observed()]
    )
    assert snapshot.coverage["matched"] == 1
    assert snapshot.findings[0].health == BusinessStateHealth.MATCHED
    assert not snapshot.blocks_unsafe_execution


def test_drift_is_first_class_and_blocks_unsafe_execution():
    snapshot = BusinessStateDriftProjector().project(
        workspace_id="ws_1",
        desired_states=[expected()],
        observations=[observed(state={"status": "disabled", "owner": "ops"})],
    )
    finding = snapshot.findings[0]
    assert finding.health == BusinessStateHealth.DRIFTED
    assert finding.finding.differing_fields == ("status",)
    assert snapshot.blocks_unsafe_execution


def test_missing_observation_is_not_a_match():
    snapshot = BusinessStateDriftProjector().project(workspace_id="ws_1", desired_states=[expected()])
    assert snapshot.findings[0].health == BusinessStateHealth.OBSERVATION_UNAVAILABLE
    assert snapshot.blocks_unsafe_execution


def test_workspace_isolation_is_fail_closed():
    snapshot = BusinessStateDriftProjector().project(
        workspace_id="ws_1",
        desired_states=[expected(workspace="ws_2")],
        observations=[observed(workspace="ws_2")],
    )
    assert snapshot.findings == ()
    assert any("workspace_mismatch" in warning for warning in snapshot.warnings)


def test_latest_observation_wins_deterministically():
    snapshot = BusinessStateDriftProjector().project(
        workspace_id="ws_1",
        desired_states=[expected()],
        observations=[
            observed(state={"status": "disabled"}, observed_at="2026-10-07T07:00:00Z", evidence="old"),
            observed(state={"status": "active", "owner": "ops"}, observed_at="2026-10-07T08:00:00Z", evidence="new"),
        ],
    )
    assert snapshot.findings[0].health == BusinessStateHealth.MATCHED
    assert snapshot.findings[0].finding.evidence_refs == ("desired:42", "new")


def test_graph_resolution_requires_explicit_node_id():
    class Node:
        node_id = "asset:42"
        node_type = type("T", (), {"value": "system"})()

    class Graph:
        workspace_id = "ws_1"
        nodes = (Node(),)

    snapshot = BusinessStateDriftProjector().project(
        workspace_id="ws_1", desired_states=[expected()], observations=[observed()], graph=Graph()
    )
    assert snapshot.findings[0].resource.node_id == "asset:42"
    assert snapshot.findings[0].resource.node_type == "system"


def test_graph_does_not_match_by_name():
    class Node:
        node_id = "different-id"
        node_type = type("T", (), {"value": "system"})()

    class Graph:
        workspace_id = "ws_1"
        nodes = (Node(),)

    snapshot = BusinessStateDriftProjector().project(
        workspace_id="ws_1", desired_states=[expected()], observations=[observed()], graph=Graph()
    )
    assert snapshot.findings[0].resource.node_id is None


def test_empty_desired_state_is_explicit():
    snapshot = BusinessStateDriftProjector().project(workspace_id="ws_1")
    assert snapshot.coverage["findings"] == 0
    assert "no_desired_state" in snapshot.warnings
