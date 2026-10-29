from pathlib import Path
import sqlite3
import pytest

from services.ai_provider import AIReceipt
from services.intelligence_ai_chain_recovery import IntelligenceAIChainRecoveryRepository
from services.intelligence_ai_fabric import AIReceiptRepository, ingest_ai_receipt_as_interpretation
from services.intelligence_decision_engine import IntelligenceDecisionEngine, IntelligenceDecisionPolicy
from services.intelligence_decision_replay import IntelligenceDecisionReplayRepository
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph
from services.intelligence_evidence_persistence import IntelligenceEvidenceRepository


def _receipt(model="model-v1"):
    return AIReceipt(status="succeeded", provider="cloudflare_ai", model=model, raw_output="ok", structured_output={"meaning":"replenish"}, usage=None, correlation_id="corr-1", request_fingerprint="req-1")


def _build(tmp_path):
    ai = AIReceiptRepository(Path(tmp_path) / "ai.sqlite")
    graph = IntelligenceEvidenceGraph()
    signal = graph.add_node(node_type="signal", workspace_id="ws", payload={"x": 1}, provenance={"source": "test"})
    _, interpretation = ingest_ai_receipt_as_interpretation(graph=graph, repository=ai, receipt=_receipt(), workspace_id="ws", signal_node_id=signal.node_id)
    result = IntelligenceDecisionEngine().construct(
        graph=graph, interpretation_node_id=interpretation.node_id,
        policy=IntelligenceDecisionPolicy(policy_id="p", workspace_id="ws"),
        decision={"decision":"replenish"},
        recommendation={"action_type":"supply_chain.replenish","risk_class":"LOW","confidence":.95,"uncertainty":0.0,"evidence_refs":["x"],"autonomy_level":"L2"},
    )
    replay = IntelligenceDecisionReplayRepository(Path(tmp_path) / "replay.sqlite")
    snap = replay.capture(graph=graph, result=result)
    evidence = IntelligenceEvidenceRepository(Path(tmp_path) / "evidence.sqlite")
    evidence.append_snapshot(graph.snapshot())
    chains = IntelligenceAIChainRecoveryRepository(Path(tmp_path) / "chains.sqlite")
    return ai, graph, replay, snap, evidence, chains


def test_chain_manifest_binds_receipt_interpretation_decision_and_recommendation(tmp_path):
    ai, graph, replay, snap, evidence, chains = _build(tmp_path)
    manifest = chains.capture(graph=graph, decision_replay=replay, decision_replay_id=snap.replay_id, ai_receipt_repository=ai, workspace_id="ws")
    assert manifest.ai_receipt_fingerprint
    assert manifest.decision_replay_id == snap.replay_id
    assert manifest.interpretation_fingerprint
    chains.close(); evidence.close(); replay.close(); ai.close()


def test_chain_recovers_after_all_repositories_restart(tmp_path):
    ai, graph, replay, snap, evidence, chains = _build(tmp_path)
    manifest = chains.capture(graph=graph, decision_replay=replay, decision_replay_id=snap.replay_id, ai_receipt_repository=ai, workspace_id="ws")
    chains.close(); evidence.close(); replay.close(); ai.close()
    ai = AIReceiptRepository(Path(tmp_path) / "ai.sqlite")
    replay = IntelligenceDecisionReplayRepository(Path(tmp_path) / "replay.sqlite")
    evidence = IntelligenceEvidenceRepository(Path(tmp_path) / "evidence.sqlite")
    chains = IntelligenceAIChainRecoveryRepository(Path(tmp_path) / "chains.sqlite")
    recovered = chains.recover(manifest.chain_id, evidence_repository=evidence, decision_replay=replay, ai_receipt_repository=ai, workspace_id="ws")
    assert recovered.manifest.ai_receipt_fingerprint == recovered.decision_replay.ai_receipt_fingerprint
    assert recovered.graph.get_node(manifest.interpretation_node_id).provenance["ai_receipt_fingerprint"] == manifest.ai_receipt_fingerprint
    chains.close(); evidence.close(); replay.close(); ai.close()


def test_chain_rejects_tampered_persisted_interpretation(tmp_path):
    ai, graph, replay, snap, evidence, chains = _build(tmp_path)
    manifest = chains.capture(graph=graph, decision_replay=replay, decision_replay_id=snap.replay_id, ai_receipt_repository=ai, workspace_id="ws")
    chains.close(); evidence.close(); replay.close(); ai.close()
    conn = sqlite3.connect(Path(tmp_path) / "evidence.sqlite")
    conn.execute("UPDATE intelligence_evidence_nodes SET payload_json=? WHERE node_id=?", ('{"meaning":"tampered"}', manifest.interpretation_node_id)); conn.commit(); conn.close()
    ai = AIReceiptRepository(Path(tmp_path) / "ai.sqlite"); replay = IntelligenceDecisionReplayRepository(Path(tmp_path) / "replay.sqlite"); evidence = IntelligenceEvidenceRepository(Path(tmp_path) / "evidence.sqlite"); chains = IntelligenceAIChainRecoveryRepository(Path(tmp_path) / "chains.sqlite")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        chains.recover(manifest.chain_id, evidence_repository=evidence, decision_replay=replay, ai_receipt_repository=ai, workspace_id="ws")
    chains.close(); evidence.close(); replay.close(); ai.close()


def test_chain_rejects_cross_workspace_recovery(tmp_path):
    ai, graph, replay, snap, evidence, chains = _build(tmp_path)
    manifest = chains.capture(graph=graph, decision_replay=replay, decision_replay_id=snap.replay_id, ai_receipt_repository=ai, workspace_id="ws")
    with pytest.raises(PermissionError): chains.get(manifest.chain_id, workspace_id="other")
    chains.close(); evidence.close(); replay.close(); ai.close()


def test_chain_manifest_tamper_is_detected(tmp_path):
    ai, graph, replay, snap, evidence, chains = _build(tmp_path)
    manifest = chains.capture(graph=graph, decision_replay=replay, decision_replay_id=snap.replay_id, ai_receipt_repository=ai, workspace_id="ws")
    chains.close(); evidence.close(); replay.close(); ai.close()
    conn = sqlite3.connect(Path(tmp_path) / "chains.sqlite")
    conn.execute("UPDATE intelligence_ai_chain_manifests SET ai_receipt_fingerprint='tampered' WHERE chain_id=?", (manifest.chain_id,)); conn.commit(); conn.close()
    chains = IntelligenceAIChainRecoveryRepository(Path(tmp_path) / "chains.sqlite")
    with pytest.raises(ValueError, match="manifest fingerprint mismatch"):
        chains.get(manifest.chain_id, workspace_id="ws")
    chains.close()


def test_chain_manifest_persists_interpretation_semantic_identity(tmp_path):
    ai, graph, replay, snap, evidence, chains = _build(tmp_path)
    manifest = chains.capture(graph=graph, decision_replay=replay, decision_replay_id=snap.replay_id, ai_receipt_repository=ai, workspace_id="ws")
    assert manifest.interpretation_semantic_fingerprint == snap.ai_interpretation_semantic_fingerprint
    chains.close(); evidence.close(); replay.close(); ai.close()


def test_chain_recovery_rejects_semantic_interpretation_tamper(tmp_path):
    ai, graph, replay, snap, evidence, chains = _build(tmp_path)
    manifest = chains.capture(graph=graph, decision_replay=replay, decision_replay_id=snap.replay_id, ai_receipt_repository=ai, workspace_id="ws")
    chains.close(); evidence.close(); replay.close(); ai.close()
    conn=sqlite3.connect(Path(tmp_path)/"chains.sqlite")
    conn.execute("UPDATE intelligence_ai_chain_manifests SET interpretation_semantic_fingerprint='tampered' WHERE chain_id=?",(manifest.chain_id,)); conn.commit(); conn.close()
    ai=AIReceiptRepository(Path(tmp_path)/"ai.sqlite"); replay=IntelligenceDecisionReplayRepository(Path(tmp_path)/"replay.sqlite"); evidence=IntelligenceEvidenceRepository(Path(tmp_path)/"evidence.sqlite"); chains=IntelligenceAIChainRecoveryRepository(Path(tmp_path)/"chains.sqlite")
    with pytest.raises(ValueError, match="manifest fingerprint mismatch"):
        chains.get(manifest.chain_id, workspace_id="ws")
    chains.close(); evidence.close(); replay.close(); ai.close()
