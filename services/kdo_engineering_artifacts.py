"""Tenant-scoped immutable artifacts for KDO's AI Engineering Vertical.

This local SQLite adapter stores snapshot/patch/test-log bytes and integrity
metadata. It is not a Git client, sandbox, authorization grant, or production
multi-instance store. Callers must authorize access before invoking it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import threading
import uuid
from pathlib import PurePosixPath
from typing import Literal

ArtifactKind = Literal["repository_snapshot", "patch", "test_log", "build_log", "review_report"]
_ALLOWED_KINDS = {"repository_snapshot", "patch", "test_log", "build_log", "review_report"}


class EngineeringArtifactError(ValueError):
    """Invalid artifact metadata or content."""


class EngineeringArtifactNotFound(LookupError):
    """Artifact is absent from the requested tenant/workspace scope."""


class EngineeringArtifactConflict(RuntimeError):
    """Artifact ID collision or stored content integrity failure."""


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EngineeringArtifactError(f"{name}_required")
    return value.strip()


def _validate_relative_path(path: str) -> str:
    path = _nonempty(path, "relative_path").replace("\\", "/")
    raw_parts = path.split("/")
    p = PurePosixPath(path)
    if p.is_absolute() or any(part in ("", ".", "..") for part in raw_parts) or "\x00" in path:
        raise EngineeringArtifactError("unsafe_artifact_path")
    if len(path) > 1024:
        raise EngineeringArtifactError("artifact_path_too_long")
    return str(p)


@dataclass(frozen=True)
class EngineeringArtifact:
    artifact_id: str
    tenant_id: str
    workspace_id: str
    task_id: str
    kind: ArtifactKind
    filename: str
    sha256: str
    size_bytes: int
    created_at: str
    base_revision: str | None = None
    relative_path: str | None = None
    metadata: dict | None = None


class SQLiteEngineeringArtifactRepository:
    """Small immutable blob/manifest store for local development and tests."""

    def __init__(self, db_path: str = ":memory:", *, max_artifact_bytes: int = 5_000_000) -> None:
        if max_artifact_bytes < 1:
            raise ValueError("max_artifact_bytes_must_be_positive")
        self.max_artifact_bytes = max_artifact_bytes
        self._lock = threading.RLock()
        self._db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if db_path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = FULL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS kdo_engineering_artifacts (
                artifact_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('repository_snapshot','patch','test_log','build_log','review_report')),
                filename TEXT NOT NULL,
                relative_path TEXT,
                base_revision TEXT,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                content BLOB NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(tenant_id, workspace_id, task_id, kind, sha256, relative_path)
            );
            CREATE INDEX IF NOT EXISTS idx_kdo_artifacts_scope_task
              ON kdo_engineering_artifacts(tenant_id, workspace_id, task_id, created_at);
        """)

    def put(self, *, tenant_id: str, workspace_id: str, task_id: str,
            kind: ArtifactKind, filename: str, content: bytes,
            base_revision: str | None = None, relative_path: str | None = None,
            metadata: dict | None = None, artifact_id: str | None = None) -> EngineeringArtifact:
        tenant_id = _nonempty(tenant_id, "tenant_id")
        workspace_id = _nonempty(workspace_id, "workspace_id")
        task_id = _nonempty(task_id, "task_id")
        filename = _nonempty(filename, "filename")
        if kind not in _ALLOWED_KINDS:
            raise EngineeringArtifactError("unsupported_artifact_kind")
        if not isinstance(content, bytes):
            raise EngineeringArtifactError("content_must_be_bytes")
        if len(content) > self.max_artifact_bytes:
            raise EngineeringArtifactError("artifact_size_limit_exceeded")
        if "\x00" in filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
            raise EngineeringArtifactError("invalid_artifact_filename")
        if relative_path is not None:
            relative_path = _validate_relative_path(relative_path)
        if kind == "patch" and not content:
            raise EngineeringArtifactError("empty_patch_rejected")
        if kind == "repository_snapshot" and not base_revision:
            raise EngineeringArtifactError("snapshot_base_revision_required")
        if base_revision is not None:
            base_revision = _nonempty(base_revision, "base_revision")
        if metadata is not None and not isinstance(metadata, dict):
            raise EngineeringArtifactError("metadata_must_be_object")
        # JSON round-trip rejects unserializable values and prevents caller mutation.
        metadata_json = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(content).hexdigest()
        aid = artifact_id or f"kdo-art-{uuid.uuid4().hex}"
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                self._db.execute("""INSERT INTO kdo_engineering_artifacts
                    (artifact_id,tenant_id,workspace_id,task_id,kind,filename,relative_path,base_revision,sha256,size_bytes,content,metadata_json,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (aid, tenant_id, workspace_id, task_id, kind, filename, relative_path,
                     base_revision, digest, len(content), sqlite3.Binary(content), metadata_json, created_at))
            except sqlite3.IntegrityError as exc:
                existing = self._db.execute("SELECT * FROM kdo_engineering_artifacts WHERE artifact_id=?", (aid,)).fetchone()
                if existing and existing["tenant_id"] == tenant_id and existing["workspace_id"] == workspace_id and existing["sha256"] == digest:
                    return self._manifest(existing)
                raise EngineeringArtifactConflict("artifact_id_or_manifest_collision") from exc
        return EngineeringArtifact(aid, tenant_id, workspace_id, task_id, kind, filename, digest,
                                   len(content), created_at, base_revision, relative_path, json.loads(metadata_json))

    @staticmethod
    def _manifest(row: sqlite3.Row) -> EngineeringArtifact:
        return EngineeringArtifact(row["artifact_id"], row["tenant_id"], row["workspace_id"], row["task_id"],
            row["kind"], row["filename"], row["sha256"], row["size_bytes"], row["created_at"],
            row["base_revision"], row["relative_path"], json.loads(row["metadata_json"]))

    def get(self, artifact_id: str, *, tenant_id: str, workspace_id: str) -> tuple[EngineeringArtifact, bytes]:
        if not tenant_id or not workspace_id:
            raise EngineeringArtifactError("tenant_and_workspace_scope_required")
        with self._lock:
            row = self._db.execute("SELECT * FROM kdo_engineering_artifacts WHERE artifact_id=? AND tenant_id=? AND workspace_id=?",
                                   (artifact_id, tenant_id, workspace_id)).fetchone()
        if row is None:
            raise EngineeringArtifactNotFound("engineering_artifact_not_found")
        content = bytes(row["content"])
        if len(content) != row["size_bytes"] or hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise EngineeringArtifactConflict("stored_artifact_integrity_mismatch")
        return self._manifest(row), content

    def list_task(self, task_id: str, *, tenant_id: str, workspace_id: str, limit: int = 100) -> list[EngineeringArtifact]:
        if not tenant_id or not workspace_id:
            raise EngineeringArtifactError("tenant_and_workspace_scope_required")
        if not 1 <= limit <= 500:
            raise EngineeringArtifactError("limit_must_be_between_1_and_500")
        with self._lock:
            rows = self._db.execute("""SELECT * FROM kdo_engineering_artifacts
                WHERE task_id=? AND tenant_id=? AND workspace_id=? ORDER BY created_at,artifact_id LIMIT ?""",
                (task_id, tenant_id, workspace_id, limit)).fetchall()
        return [self._manifest(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()
