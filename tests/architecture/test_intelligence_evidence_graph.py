from __future__ import annotations

import ast
from pathlib import Path

import pytest

from services.intelligence_evidence_graph import IntelligenceEvidenceGraph

ROOT = Path(__file__).resolve().parents[2]


def _graph() -> tuple[IntelligenceEvidenceGraph, str]:
    graph = IntelligenceEvidenceGraph()
    signal = graph.add_node(
        node_type="signal", workspace_id="ws-1", payload={"kind": "metric", "value": 92},
        provenance={"source": "monitoring", "source_id": "m-1"}, node_id="s-1",
    )
    interpretation = graph.add_node(
        node_type="interpretation", workspace_id="ws-1", payload={"meaning": "load elevated"},
        provenance={"signal_node_id": signal.node_id, "model": "test-model"}, node_id="i-1",
    )
    graph.link(signal.node_id, interpretation.node_id)
    decision = graph.add_node(
        node_type="decision", workspace_id="ws-1", payload={"disposition": "recommend"},
        provenance={"interpretation_node_id": interpretation.node_id, "policy_version": "p1"}, node_id="d-1",
    )
    graph.link(interpretation.node_id, decision.node_id)
    recommendation = graph.add_node(
        node_type="recommendation", workspace_id="ws-1", payload={"action_type": "notify_operator"},
        provenance={"decision_node_id": decision.node_id}, node_id="r-1",
    )
    graph.link(decision.node_id, recommendation.node_id)
    action = graph.add_action_intent(
        workspace_id="ws-1",
        action_intent={"action_type": "notify_operator", "idempotency_key": "k-1"},
        recommendation_node_id=recommendation.node_id,
    )
    return graph, action.node_id


def test_complete_lineage_is_signal_to_action_intent():
    graph, action_id = _graph()
    result = graph.validate_lineage(action_id)
    assert result["complete"] is True
    assert [node["node_type"] for node in result["lineage"]] == [
        "signal", "interpretation", "decision", "recommendation", "action_intent"
    ]


def test_action_intent_requires_recommendation_parent():
    graph = IntelligenceEvidenceGraph()
    with pytest.raises(ValueError, match="unknown evidence node|recommendation"):
        graph.add_action_intent(
            workspace_id="ws-1",
            action_intent={"action_type": "x", "idempotency_key": "k"},
            recommendation_node_id="missing",
        )


def test_cross_workspace_links_are_rejected():
    graph = IntelligenceEvidenceGraph()
    a = graph.add_node(node_type="signal", workspace_id="ws-a", payload={"x": 1}, provenance={"source": "a"}, node_id="a")
    b = graph.add_node(node_type="interpretation", workspace_id="ws-b", payload={"x": 1}, provenance={"signal_node_id": "a"}, node_id="b")
    with pytest.raises(ValueError, match="workspace"):
        graph.link(a.node_id, b.node_id)


def test_invalid_lineage_shape_is_rejected():
    graph = IntelligenceEvidenceGraph()
    signal = graph.add_node(node_type="signal", workspace_id="ws-1", payload={"x": 1}, provenance={"source": "a"}, node_id="s")
    rec = graph.add_node(node_type="recommendation", workspace_id="ws-1", payload={"action_type": "x"}, provenance={"decision_node_id": "d"}, node_id="r")
    action = graph.add_action_intent(
        workspace_id="ws-1", action_intent={"action_type": "x", "idempotency_key": "k"},
        recommendation_node_id=rec.node_id,
    )
    with pytest.raises(ValueError, match="requires exactly one parent"):
        graph.validate_lineage(action.node_id)


def test_graph_has_no_execution_authority_dependency():
    source = (ROOT / "services" / "intelligence_evidence_graph.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        (node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    ]
    assert not any("action_kernel" in module for module in imports)
    assert "execute(" not in source
    assert "enqueue(" not in source
