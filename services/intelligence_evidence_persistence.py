"""Durable persistence and read-model projection for the Phase 5 Evidence Graph.

This is deliberately independent of the Action Kernel. It provides an append-only
SQLite journal and a deterministic read-model projection for Command Center use.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from services.intelligence_evidence_graph import IntelligenceEvidenceGraph, SCHEMA_VERSION


class IntelligenceEvidenceRepository:
    """Restart-safe persistence boundary for evidence graph nodes and edges."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS intelligence_evidence_nodes (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS intelligence_evidence_edges (
                edge_id TEXT PRIMARY KEY,
                source_node_id TEXT NOT NULL,
                target_node_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                target_type TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                created_at TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                FOREIGN KEY(source_node_id) REFERENCES intelligence_evidence_nodes(node_id),
                FOREIGN KEY(target_node_id) REFERENCES intelligence_evidence_nodes(node_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ieg_nodes_workspace ON intelligence_evidence_nodes(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_ieg_edges_target ON intelligence_evidence_edges(target_node_id);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def append_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        if snapshot.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported evidence graph schema")
        with self._conn:
            for node in snapshot.get("nodes", []):
                self._conn.execute(
                    """INSERT INTO intelligence_evidence_nodes
                    (node_id,node_type,workspace_id,payload_json,provenance_json,created_at,schema_version,fingerprint)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(node_id) DO NOTHING""",
                    (node["node_id"], node["node_type"], node["workspace_id"],
                     json.dumps(node["payload"], sort_keys=True), json.dumps(node["provenance"], sort_keys=True),
                     node["created_at"], node["schema_version"], node["fingerprint"]),
                )
            for edge in snapshot.get("edges", []):
                self._conn.execute(
                    """INSERT INTO intelligence_evidence_edges
                    (edge_id,source_node_id,target_node_id,source_type,target_type,workspace_id,relation,created_at,schema_version)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(edge_id) DO NOTHING""",
                    (edge["edge_id"], edge["source_node_id"], edge["target_node_id"], edge["source_type"],
                     edge["target_type"], edge["workspace_id"], edge["relation"], edge["created_at"], edge["schema_version"]),
                )

    def load_workspace(self, workspace_id: str) -> IntelligenceEvidenceGraph:
        graph = IntelligenceEvidenceGraph()
        nodes = self._conn.execute(
            "SELECT * FROM intelligence_evidence_nodes WHERE workspace_id=? ORDER BY created_at,node_id",
            (workspace_id,),
        ).fetchall()
        for row in nodes:
            graph.add_node(
                node_type=row["node_type"], workspace_id=row["workspace_id"],
                payload=json.loads(row["payload_json"]), provenance=json.loads(row["provenance_json"]),
                node_id=row["node_id"], created_at=row["created_at"],
            )
            loaded = graph.get_node(row["node_id"])
            if loaded.to_dict()["fingerprint"] != row["fingerprint"]:
                raise ValueError("stored evidence node fingerprint mismatch")
        edges = self._conn.execute(
            "SELECT * FROM intelligence_evidence_edges WHERE workspace_id=? ORDER BY created_at,edge_id",
            (workspace_id,),
        ).fetchall()
        for row in edges:
            graph.link(row["source_node_id"], row["target_node_id"], relation=row["relation"])
        return graph

    def project_action_intents(self, workspace_id: str) -> list[dict[str, Any]]:
        """Project complete ActionIntent lineage for Command Center reads."""
        graph = self.load_workspace(workspace_id)
        rows = self._conn.execute(
            """SELECT node_id FROM intelligence_evidence_nodes
               WHERE workspace_id=? AND node_type='action_intent'
               ORDER BY created_at,node_id""",
            (workspace_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            lineage = graph.validate_lineage(row["node_id"])
            result.append(lineage)
        return result
