"""Phase 5.9 — recoverable AI interpretation/decision chain.

The receipt, interpretation, decision and recommendation remain separate
append-only records, but this module persists a deterministic chain manifest
that binds their identities. Recovery verifies every member and every lineage
edge before returning a historical chain. It never governs or executes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from services.intelligence_ai_fabric import AIReceiptRepository, AIReceiptReplayVerifier
from services.intelligence_decision_replay import IntelligenceDecisionReplayRepository, DecisionReplaySnapshot
from services.intelligence_evidence_graph import IntelligenceEvidenceGraph

SCHEMA = "KDO.IntelligenceAIChainRecovery/v1"


def _fp(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _node_fingerprint(node: Any) -> str:
    return str(node.to_dict()["fingerprint"])


@dataclass(frozen=True)
class AIChainManifest:
    chain_id: str
    workspace_id: str
    signal_node_id: str
    interpretation_node_id: str
    decision_node_id: str
    recommendation_node_id: str
    signal_fingerprint: str
    interpretation_fingerprint: str
    decision_fingerprint: str
    recommendation_fingerprint: str
    ai_receipt_fingerprint: str
    interpretation_semantic_fingerprint: str
    decision_replay_id: str
    manifest_fingerprint: str
    schema_version: str = SCHEMA


@dataclass(frozen=True)
class RecoveredAIChain:
    manifest: AIChainManifest
    graph: IntelligenceEvidenceGraph
    decision_replay: DecisionReplaySnapshot


class IntelligenceAIChainRecoveryRepository:
    """Durable manifest binding AI receipt evidence to its historical decision."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS intelligence_ai_chain_manifests (
                chain_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                signal_node_id TEXT NOT NULL,
                interpretation_node_id TEXT NOT NULL,
                decision_node_id TEXT NOT NULL,
                recommendation_node_id TEXT NOT NULL,
                signal_fingerprint TEXT NOT NULL,
                interpretation_fingerprint TEXT NOT NULL,
                decision_fingerprint TEXT NOT NULL,
                recommendation_fingerprint TEXT NOT NULL,
                ai_receipt_fingerprint TEXT NOT NULL,
                interpretation_semantic_fingerprint TEXT NOT NULL,
                decision_replay_id TEXT NOT NULL,
                manifest_fingerprint TEXT NOT NULL UNIQUE,
                schema_version TEXT NOT NULL
            )"""
        )
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(intelligence_ai_chain_manifests)").fetchall()}
        if "interpretation_semantic_fingerprint" not in existing:
            self._conn.execute("ALTER TABLE intelligence_ai_chain_manifests ADD COLUMN interpretation_semantic_fingerprint TEXT")
            self._conn.execute("UPDATE intelligence_ai_chain_manifests SET interpretation_semantic_fingerprint='' WHERE interpretation_semantic_fingerprint IS NULL")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_chain_workspace ON intelligence_ai_chain_manifests(workspace_id)")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def capture(
        self,
        *,
        graph: IntelligenceEvidenceGraph,
        decision_replay: IntelligenceDecisionReplayRepository,
        decision_replay_id: str,
        ai_receipt_repository: AIReceiptRepository,
        workspace_id: str,
    ) -> AIChainManifest:
        snapshot = decision_replay.replay(decision_replay_id, ai_receipt_repository=ai_receipt_repository, workspace_id=workspace_id)
        decision = graph.get_node(snapshot.decision["node_id"])
        recommendation = graph.get_node(snapshot.recommendation["node_id"])
        interpretation_id = str(decision.provenance.get("interpretation_node_id") or "")
        interpretation = graph.get_node(interpretation_id)
        signal_id = str(interpretation.provenance.get("signal_node_id") or "")
        signal = graph.get_node(signal_id)
        if any(node.workspace_id != workspace_id for node in (signal, interpretation, decision, recommendation)):
            raise PermissionError("AI chain workspace mismatch")
        if snapshot.ai_receipt_fingerprint != interpretation.provenance.get("ai_receipt_fingerprint"):
            raise ValueError("AI chain receipt identity mismatch")
        interpretation_semantic = str(interpretation.provenance.get("ai_interpretation_semantic_fingerprint") or "")
        if snapshot.ai_interpretation_semantic_fingerprint != interpretation_semantic:
            raise ValueError("AI chain interpretation semantic identity mismatch")
        AIReceiptReplayVerifier(ai_receipt_repository).verify_interpretation(
            graph=graph, interpretation_node_id=interpretation.node_id, workspace_id=workspace_id
        )
        if recommendation.provenance.get("decision_node_id") != decision.node_id:
            raise ValueError("AI chain recommendation lineage mismatch")
        if snapshot.decision_fingerprint != decision.provenance.get("decision_fingerprint"):
            raise ValueError("AI chain decision fingerprint mismatch")

        required_edges = {
            (signal.node_id, interpretation.node_id, "supports"),
            (interpretation.node_id, decision.node_id, "supports"),
            (decision.node_id, recommendation.node_id, "recommends"),
        }
        actual_edges = {
            (edge["source_node_id"], edge["target_node_id"], edge["relation"])
            for edge in graph.snapshot()["edges"]
        }
        if not required_edges.issubset(actual_edges):
            raise ValueError("AI chain lineage edges are incomplete")

        material = {
            "workspace_id": workspace_id,
            "signal_node_id": signal.node_id,
            "interpretation_node_id": interpretation.node_id,
            "decision_node_id": decision.node_id,
            "recommendation_node_id": recommendation.node_id,
            "signal_fingerprint": _node_fingerprint(signal),
            "interpretation_fingerprint": _node_fingerprint(interpretation),
            "decision_fingerprint": _node_fingerprint(decision),
            "recommendation_fingerprint": _node_fingerprint(recommendation),
            "ai_receipt_fingerprint": snapshot.ai_receipt_fingerprint,
            "interpretation_semantic_fingerprint": interpretation_semantic,
            "decision_replay_id": snapshot.replay_id,
            "schema_version": SCHEMA,
        }
        manifest_fp = _fp(material)
        chain_id = "iac_" + manifest_fp[:24]
        manifest = AIChainManifest(chain_id=chain_id, manifest_fingerprint=manifest_fp, **material)
        with self._conn:
            self._conn.execute(
                """INSERT INTO intelligence_ai_chain_manifests
                (chain_id,workspace_id,signal_node_id,interpretation_node_id,decision_node_id,recommendation_node_id,
                 signal_fingerprint,interpretation_fingerprint,decision_fingerprint,recommendation_fingerprint,
                 ai_receipt_fingerprint,interpretation_semantic_fingerprint,decision_replay_id,manifest_fingerprint,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(manifest_fingerprint) DO NOTHING""",
                (manifest.chain_id, manifest.workspace_id, manifest.signal_node_id, manifest.interpretation_node_id,
                 manifest.decision_node_id, manifest.recommendation_node_id, manifest.signal_fingerprint,
                 manifest.interpretation_fingerprint, manifest.decision_fingerprint, manifest.recommendation_fingerprint,
                 manifest.ai_receipt_fingerprint, manifest.interpretation_semantic_fingerprint, manifest.decision_replay_id, manifest.manifest_fingerprint, SCHEMA),
            )
        return manifest

    def get(self, chain_id: str, *, workspace_id: str) -> AIChainManifest:
        row = self._conn.execute(
            "SELECT * FROM intelligence_ai_chain_manifests WHERE chain_id=? AND workspace_id=?",
            (chain_id, workspace_id),
        ).fetchone()
        if row is None:
            raise PermissionError("AI chain is absent or outside workspace scope")
        material = {k: row[k] for k in (
            "workspace_id", "signal_node_id", "interpretation_node_id", "decision_node_id", "recommendation_node_id",
            "signal_fingerprint", "interpretation_fingerprint", "decision_fingerprint", "recommendation_fingerprint",
            "ai_receipt_fingerprint", "interpretation_semantic_fingerprint", "decision_replay_id", "schema_version"
        )}
        if _fp(material) != row["manifest_fingerprint"]:
            raise ValueError("stored AI chain manifest fingerprint mismatch")
        return AIChainManifest(chain_id=row["chain_id"], manifest_fingerprint=row["manifest_fingerprint"], **material)

    def recover(
        self,
        chain_id: str,
        *,
        evidence_repository: Any,
        decision_replay: IntelligenceDecisionReplayRepository,
        ai_receipt_repository: AIReceiptRepository,
        workspace_id: str,
    ) -> RecoveredAIChain:
        manifest = self.get(chain_id, workspace_id=workspace_id)
        graph = evidence_repository.load_workspace(workspace_id)
        nodes = [graph.get_node(node_id) for node_id in (
            manifest.signal_node_id, manifest.interpretation_node_id,
            manifest.decision_node_id, manifest.recommendation_node_id
        )]
        fingerprints = [manifest.signal_fingerprint, manifest.interpretation_fingerprint, manifest.decision_fingerprint, manifest.recommendation_fingerprint]
        for node, expected in zip(nodes, fingerprints):
            if _node_fingerprint(node) != expected:
                raise ValueError(f"AI chain historical node fingerprint mismatch: {node.node_type}")
        replayed = decision_replay.replay(
            manifest.decision_replay_id,
            ai_receipt_repository=ai_receipt_repository,
            workspace_id=workspace_id,
        )
        if replayed.ai_receipt_fingerprint != manifest.ai_receipt_fingerprint:
            raise ValueError("AI chain historical receipt mismatch")
        if replayed.ai_interpretation_semantic_fingerprint != manifest.interpretation_semantic_fingerprint:
            raise ValueError("AI chain historical interpretation semantic mismatch")
        if replayed.decision_fingerprint != manifest.decision_fingerprint and replayed.decision_fingerprint != graph.get_node(manifest.decision_node_id).provenance.get("decision_fingerprint"):
            raise ValueError("AI chain historical decision mismatch")
        AIReceiptReplayVerifier(ai_receipt_repository).verify_interpretation(
            graph=graph, interpretation_node_id=manifest.interpretation_node_id, workspace_id=workspace_id
        )
        return RecoveredAIChain(manifest=manifest, graph=graph, decision_replay=replayed)


__all__ = ["SCHEMA", "AIChainManifest", "RecoveredAIChain", "IntelligenceAIChainRecoveryRepository"]
