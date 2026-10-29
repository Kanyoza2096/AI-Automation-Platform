from services.intelligence_evidence_graph import EvidenceNode, IntelligenceEvidenceGraph
from services.intelligence_policy_governance import (
    GovernanceDisposition,
    IntelligencePolicy,
    IntelligencePolicyGovernor,
    RiskClass,
)


def _graph_with_recommendation(payload=None):
    graph = IntelligenceEvidenceGraph()
    s = graph.add_node(node_type="signal", workspace_id="ws-1", payload={"event": "x"}, provenance={"source": "monitor"}, node_id="s")
    i = graph.add_node(node_type="interpretation", workspace_id="ws-1", payload={"meaning": "x"}, provenance={"signal_node_id": "s"}, node_id="i")
    graph.link("s", "i")
    d = graph.add_node(node_type="decision", workspace_id="ws-1", payload={"disposition": "act"}, provenance={"interpretation_node_id": "i"}, node_id="d")
    graph.link("i", "d")
    r = graph.add_node(
        node_type="recommendation", workspace_id="ws-1",
        payload=payload or {
            "action_type": "notify",
            "risk_class": "LOW",
            "confidence": 0.91,
            "uncertainty": 0.0,
            "evidence_refs": ["s"],
            "autonomy_level": "L2",
        },
        provenance={"decision_node_id": "d"}, node_id="r",
    )
    graph.link("d", "r")
    return graph


def _policy(**kwargs):
    return IntelligencePolicy(policy_id="pol-1", workspace_id="ws-1", **kwargs)


def test_low_risk_high_confidence_can_create_governed_action_intent():
    graph = _graph_with_recommendation()
    evaluation, action = IntelligencePolicyGovernor().govern_to_action_intent(
        graph=graph, recommendation_node_id="r", policy=_policy(), idempotency_key="k1"
    )
    assert evaluation.disposition is GovernanceDisposition.APPROVED
    assert action is not None
    assert action.payload["governance"]["policy_id"] == "pol-1"
    assert action.payload["governance"]["disposition"] == "APPROVED"


def test_confidence_below_risk_threshold_is_rejected():
    graph = _graph_with_recommendation({
        "action_type": "notify", "risk_class": "HIGH", "confidence": 0.89,
        "uncertainty": 0.0, "evidence_refs": ["s"], "autonomy_level": "L2",
    })
    evaluation, action = IntelligencePolicyGovernor().govern_to_action_intent(
        graph=graph, recommendation_node_id="r", policy=_policy(), idempotency_key="k2"
    )
    assert evaluation.disposition is GovernanceDisposition.REJECTED
    assert "confidence_below_threshold" in evaluation.reasons
    assert action is None


def test_uncertainty_is_never_treated_as_success():
    graph = _graph_with_recommendation({
        "action_type": "notify", "risk_class": "LOW", "confidence": 0.99,
        "uncertainty": 0.01, "evidence_refs": ["s"], "autonomy_level": "L2",
    })
    evaluation, action = IntelligencePolicyGovernor().govern_to_action_intent(
        graph=graph, recommendation_node_id="r", policy=_policy(), idempotency_key="k3"
    )
    assert evaluation.disposition is GovernanceDisposition.HOLD_UNCERTAIN
    assert action is None


def test_high_risk_requires_human_approval_and_does_not_create_action_intent():
    graph = _graph_with_recommendation({
        "action_type": "notify", "risk_class": RiskClass.HIGH.value, "confidence": 0.95,
        "uncertainty": 0.0, "evidence_refs": ["s"], "autonomy_level": "L4",
    })
    evaluation, action = IntelligencePolicyGovernor().govern_to_action_intent(
        graph=graph, recommendation_node_id="r", policy=_policy(), idempotency_key="k4"
    )
    assert evaluation.disposition is GovernanceDisposition.REQUIRES_APPROVAL
    assert evaluation.requires_human_approval is True
    assert action is None


def test_policy_allowlist_and_blocklist_are_enforced():
    graph = _graph_with_recommendation()
    policy = _policy(allowed_action_types=frozenset({"other"}), blocked_action_types=frozenset({"notify"}))
    evaluation, action = IntelligencePolicyGovernor().govern_to_action_intent(
        graph=graph, recommendation_node_id="r", policy=policy, idempotency_key="k5"
    )
    assert evaluation.disposition is GovernanceDisposition.REJECTED
    assert "action_type_not_allowed" in evaluation.reasons
    assert "action_type_blocked" in evaluation.reasons
    assert action is None


def test_workspace_scope_is_mandatory():
    graph = _graph_with_recommendation()
    policy = IntelligencePolicy(policy_id="pol-2", workspace_id="ws-2")
    try:
        IntelligencePolicyGovernor().evaluate(
            recommendation=EvidenceNode(
                node_id="r", node_type="recommendation", workspace_id="ws-1",
                payload=next(n for n in graph.snapshot()["nodes"] if n["node_id"] == "r")["payload"],
                provenance={"decision_node_id": "d"},
            ),
            policy=policy,
        )
    except ValueError as exc:
        assert "workspace" in str(exc)
    else:
        raise AssertionError("cross-workspace governance must fail")


def test_governance_module_has_no_execution_authority():
    import pathlib
    source = pathlib.Path(__file__).parents[2].joinpath("services", "intelligence_policy_governance.py").read_text()
    assert "action_kernel" not in source.lower()
    assert "provider call" not in source.lower()
    assert "provider mutation" not in source.lower()


def test_approved_governed_action_intent_survives_persistence(tmp_path):
    from services.intelligence_evidence_persistence import IntelligenceEvidenceRepository

    graph = _graph_with_recommendation()
    evaluation, action = IntelligencePolicyGovernor().govern_to_action_intent(
        graph=graph, recommendation_node_id="r", policy=_policy(), idempotency_key="persist-1"
    )
    assert evaluation.disposition is GovernanceDisposition.APPROVED
    assert action is not None

    repo = IntelligenceEvidenceRepository(tmp_path / "governance.sqlite")
    repo.append_snapshot(graph.snapshot())
    projected = repo.project_action_intents("ws-1")
    assert len(projected) == 1
    assert projected[0]["complete"] is True
    assert projected[0]["lineage"][-1]["payload"]["governance"]["policy_id"] == "pol-1"
    repo.close()
