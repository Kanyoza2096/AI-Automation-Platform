"""Staging-only trusted composition root for the KDO Engineering worker.

The worker uses the existing KDO Supabase service-client singleton, canonical
LLM Gateway, durable Action Kernel, scoped workspace API token, PostgreSQL queue,
and Phase 16.9 event ledger. This module never enables or starts the worker.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from services.kdo_engineering_worker_bootstrap import ProductionWorkerConfig


class StagingWorkerHostConfigurationError(RuntimeError):
    """Raised when the staging worker cannot be safely composed."""


def _required(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name, "") or "").strip()
    if not value:
        raise StagingWorkerHostConfigurationError("required_worker_setting_missing:" + name)
    return value


def _project_ref_from_settings(settings: Any, config: ProductionWorkerConfig,
                               env: Mapping[str, str]) -> str:
    expected = _required(env, "KDO_ENGINEERING_EXPECTED_SUPABASE_PROJECT_REF")
    if (getattr(settings, "environment", "") or "").strip().lower() not in {"staging", "test"}:
        raise StagingWorkerHostConfigurationError("worker_environment_must_be_staging")
    supabase_host = urlparse(str(getattr(settings, "supabase_url", "") or "")).hostname
    if supabase_host != f"{expected}.supabase.co":
        raise StagingWorkerHostConfigurationError("supabase_url_does_not_match_expected_staging_project")

    database_url = urlparse(config.database_url)
    host = (database_url.hostname or "").lower()
    username = database_url.username or ""
    if host == f"db.{expected}.supabase.co":
        valid_database_identity = username == "postgres"
    elif host.endswith(".pooler.supabase.com"):
        valid_database_identity = username == f"postgres.{expected}"
    else:
        valid_database_identity = False
    if not valid_database_identity:
        raise StagingWorkerHostConfigurationError("postgres_dsn_does_not_match_expected_staging_project")
    return expected


def _one_row(response: Any) -> dict[str, Any] | None:
    data = getattr(response, "data", None)
    if isinstance(data, Mapping):
        return dict(data)
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], Mapping):
        return dict(data[0])
    return None


def _build_authenticated_workflow_authorizer(
    *, db: Any, config: ProductionWorkerConfig, worker_token: str,
    connection_factory: Any,
):
    token_hash = hashlib.sha256(worker_token.encode("utf-8")).hexdigest()
    allowed_actions = frozenset({"execute_build"})

    def authorize(actor_id: str, action: str, tenant_id: str, workspace_id: str) -> bool:
        if not (
            actor_id == config.actor_id
            and tenant_id == config.tenant_id
            and workspace_id == config.workspace_id
            and action in allowed_actions
        ):
            return False
        try:
            token_response = (
                db.table("api_tokens")
                .select("id,workspace_id,permissions,expires_at,revoked")
                .eq("token_hash", token_hash)
                .eq("revoked", False)
                .maybe_single()
                .execute()
            )
            token = _one_row(token_response)
            if not token or token.get("id") != config.actor_id:
                return False
            if token.get("workspace_id") != config.workspace_id or token.get("revoked") is not False:
                return False
            permissions = {str(item).strip().lower() for item in (token.get("permissions") or [])}
            if not permissions.intersection({"write", "operator", "admin", "owner"}):
                return False
            expiry = token.get("expires_at")
            if expiry:
                parsed_expiry = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
                if parsed_expiry.tzinfo is None:
                    parsed_expiry = parsed_expiry.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) >= parsed_expiry:
                    return False

            workspace_response = (
                db.table("workspaces")
                .select("id,status")
                .eq("id", config.workspace_id)
                .maybe_single()
                .execute()
            )
            workspace = _one_row(workspace_response)
            if not workspace or workspace.get("status") != "active":
                return False

            connection = cursor = None
            try:
                connection = connection_factory()
                cursor = connection.cursor()
                cursor.execute(
                    """SELECT EXISTS(
                         SELECT 1 FROM public.kdo_engineering_tasks
                         WHERE tenant_id=%s AND workspace_id=%s
                       )""",
                    (tenant_id, workspace_id),
                )
                row = cursor.fetchone()
                return bool(row and row[0] is True)
            finally:
                if cursor is not None:
                    try:
                        cursor.close()
                    except Exception:
                        pass
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
        except Exception:
            return False

    return authorize


def build_host_arguments(
    *, config: ProductionWorkerConfig, connection_factory: Any,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build all trusted dependencies without starting polling or Docker work."""
    env = os.environ if environ is None else environ
    from config.settings import settings

    _project_ref_from_settings(settings, config, env)
    worker_token = _required(env, "KDO_ENGINEERING_WORKER_API_TOKEN")
    if not worker_token.startswith("kny_"):
        raise StagingWorkerHostConfigurationError("workspace_scoped_kny_worker_token_required")

    from services.supabase import get_service_client_from_settings
    db = get_service_client_from_settings()
    if db is None or not callable(getattr(db, "table", None)):
        raise StagingWorkerHostConfigurationError("staging_supabase_service_client_required")

    token_hash = hashlib.sha256(worker_token.encode("utf-8")).hexdigest()
    token = _one_row(
        db.table("api_tokens")
        .select("id,workspace_id,permissions,expires_at,revoked")
        .eq("token_hash", token_hash)
        .eq("revoked", False)
        .maybe_single()
        .execute()
    )
    if not token or token.get("id") != config.actor_id:
        raise StagingWorkerHostConfigurationError("worker_actor_must_match_persisted_api_token_id")
    if token.get("workspace_id") != config.workspace_id:
        raise StagingWorkerHostConfigurationError("worker_token_workspace_mismatch")
    permissions = {str(item).strip().lower() for item in (token.get("permissions") or [])}
    if not permissions.intersection({"write", "operator", "admin", "owner"}):
        raise StagingWorkerHostConfigurationError("worker_token_requires_write_permission")
    expiry = token.get("expires_at")
    if expiry:
        parsed_expiry = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
        if parsed_expiry.tzinfo is None:
            parsed_expiry = parsed_expiry.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= parsed_expiry:
            raise StagingWorkerHostConfigurationError("worker_token_expired")
    workspace = _one_row(
        db.table("workspaces")
        .select("id,status")
        .eq("id", config.workspace_id)
        .maybe_single()
        .execute()
    )
    if not workspace or workspace.get("status") != "active":
        raise StagingWorkerHostConfigurationError("worker_workspace_not_active")

    from services.action_kernel import build_default_action_kernel
    action_kernel = build_default_action_kernel(db=db, allow_in_memory=False)
    if getattr(action_kernel, "db", None) is not db or getattr(action_kernel, "allow_in_memory", False):
        raise StagingWorkerHostConfigurationError("canonical_durable_action_kernel_required")

    from ai.gateway import get_llm_gateway
    gateway = get_llm_gateway()
    if gateway is None or not callable(getattr(gateway, "complete_structured", None)):
        raise StagingWorkerHostConfigurationError("canonical_kdo_llm_gateway_required")

    from services.kdo_engineering_postgres_event_store import PostgresEngineeringEventStore
    event_store = PostgresEngineeringEventStore(connection_factory)

    capabilities_raw = _required(env, "KDO_ENGINEERING_WORKER_REPAIR_CAPABILITIES")
    repair_capabilities = frozenset(
        value.strip() for value in capabilities_raw.split(",") if value.strip()
    )
    allowed_capabilities = {
        "backend", "api", "services", "frontend", "ui", "accessibility",
        "database", "schema", "migrations", "integration", "refactoring",
        "maintainability",
    }
    if not repair_capabilities or not repair_capabilities.issubset(allowed_capabilities):
        raise StagingWorkerHostConfigurationError("worker_repair_capabilities_not_allowlisted")

    def repair_capabilities_factory(_task: Any, _steps: tuple[str, ...]):
        return repair_capabilities

    source_path = Path(_required(env, "KDO_ENGINEERING_SOURCE_REPOSITORY_PATH")).resolve()
    if not source_path.is_dir():
        raise StagingWorkerHostConfigurationError("engineering_source_repository_path_missing")
    managed_root = Path(_required(env, "KDO_ENGINEERING_MANAGED_WORKSPACE_ROOT")).resolve()
    image = _required(env, "KDO_ENGINEERING_WORKER_IMAGE")
    if "@sha256:" not in image:
        raise StagingWorkerHostConfigurationError("digest_pinned_worker_image_required")

    def positive_int(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = env.get(name, str(default)).strip()
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise StagingWorkerHostConfigurationError("invalid_worker_setting:" + name) from exc
        if not minimum <= value <= maximum:
            raise StagingWorkerHostConfigurationError("worker_setting_out_of_range:" + name)
        return value

    authorizer = _build_authenticated_workflow_authorizer(
        db=db, config=config, worker_token=worker_token,
        connection_factory=connection_factory,
    )
    return {
        "source_repository_path": source_path,
        "managed_workspace_root": managed_root,
        "pinned_worker_image": image,
        "gateway": gateway,
        "action_kernel": action_kernel,
        "authenticated_workflow_authorizer": authorizer,
        "repair_capabilities_factory": repair_capabilities_factory,
        "event_store": event_store,
        "docker_binary": env.get("KDO_ENGINEERING_DOCKER_BINARY", "docker").strip() or "docker",
        "test_profile": env.get("KDO_ENGINEERING_TEST_PROFILE", "pytest").strip() or "pytest",
        "test_timeout_seconds": positive_int("KDO_ENGINEERING_TEST_TIMEOUT_SECONDS", 120, 10, 900),
        "max_repair_attempts": positive_int("KDO_ENGINEERING_MAX_REPAIR_ATTEMPTS", 1, 0, 5),
        "memory_limit": env.get("KDO_ENGINEERING_DOCKER_MEMORY_LIMIT", "1g").strip() or "1g",
        "cpu_limit": env.get("KDO_ENGINEERING_DOCKER_CPU_LIMIT", "1.0").strip() or "1.0",
        "pids_limit": positive_int("KDO_ENGINEERING_DOCKER_PIDS_LIMIT", 128, 16, 1024),
    }
