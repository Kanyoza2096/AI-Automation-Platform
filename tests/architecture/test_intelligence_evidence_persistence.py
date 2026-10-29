from pathlib import Path

from services.intelligence_evidence_graph import IntelligenceEvidenceGraph
from services.intelligence_evidence_persistence import IntelligenceEvidenceRepository


def _snapshot():
    graph = IntelligenceEvidenceGraph()
    s = graph.add_node(node_type="signal", workspace_id="ws-1", payload={"v": 1}, provenance={"source": "monitor"}, node_id="s")
    i = graph.add_node(node_type="interpretation", workspace_id="ws-1", payload={"meaning": "high"}, provenance={"signal_node_id": "s"}, node_id="i")
    graph.link(s.node_id, i.node_id)
    d = graph.add_node(node_type="decision", workspace_id="ws-1", payload={"disposition": "recommend"}, provenance={"interpretation_node_id": "i"}, node_id="d")
    graph.link(i.node_id, d.node_id)
    r = graph.add_node(node_type="recommendation", workspace_id="ws-1", payload={"action_type": "notify"}, provenance={"decision_node_id": "d"}, node_id="r")
    graph.link(d.node_id, r.node_id)
    graph.add_action_intent(workspace_id="ws-1", action_intent={"action_type": "notify", "idempotency_key": "k"}, recommendation_node_id="r")
    return graph.snapshot()


def test_evidence_graph_survives_repository_restart(tmp_path: Path):
    db = tmp_path / "ieg.sqlite"
    repo = IntelligenceEvidenceRepository(db)
    repo.append_snapshot(_snapshot())
    repo.close()
    reopened = IntelligenceEvidenceRepository(db)
    projected = reopened.project_action_intents("ws-1")
    assert len(projected) == 1
    assert projected[0]["complete"] is True
    assert [n["node_type"] for n in projected[0]["lineage"]] == ["signal", "interpretation", "decision", "recommendation", "action_intent"]
    reopened.close()


def test_workspace_projection_isolated(tmp_path: Path):
    db = tmp_path / "ieg.sqlite"
    repo = IntelligenceEvidenceRepository(db)
    snapshot = _snapshot()
    import hashlib, json
    def fp(n):
        value = {"node_id": n["node_id"], "node_type": n["node_type"], "workspace_id": n["workspace_id"], "payload": n["payload"], "provenance": n["provenance"], "created_at": n["created_at"], "schema_version": n["schema_version"]}
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    other_nodes = [dict(n, node_id="ws2_" + n["node_id"], workspace_id="ws-2") for n in snapshot["nodes"]]
    other_nodes = [dict(n, fingerprint=fp(n)) for n in other_nodes]
    other = {**snapshot, "nodes": other_nodes, "edges": [dict(e, edge_id="ws2_" + e["edge_id"], source_node_id="ws2_" + e["source_node_id"], target_node_id="ws2_" + e["target_node_id"], workspace_id="ws-2") for e in snapshot["edges"]]}
    repo.append_snapshot(snapshot)
    repo.append_snapshot(other)
    assert len(repo.project_action_intents("ws-1")) == 1
    assert len(repo.project_action_intents("ws-2")) == 1
    repo.close()
