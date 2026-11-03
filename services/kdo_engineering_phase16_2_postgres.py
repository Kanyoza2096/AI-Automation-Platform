"""Explicit PostgreSQL composition for the governed Phase 16.2 workflow.

The host injects the connection factory and authorization policy. This module
never reads environment secrets, chooses a database, or falls back to SQLite.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from services.kdo_engineering_phase16_2 import KDOPhase162Workflow, Phase162Error
from services.kdo_engineering_postgres_repositories import (
    PostgresEngineeringTaskRepository,
    PostgresEngineeringWorkspaceRepository,
)


def create_postgres_phase162_workflow(
    *,
    connection_factory: Callable[[], Any],
    source_repository_path: str | Path,
    managed_root: str | Path,
    authorize: Callable[[str, str, str, str], bool],
) -> KDOPhase162Workflow:
    """Compose Phase 16.2 with shared PostgreSQL repositories explicitly."""
    if not callable(connection_factory):
        raise Phase162Error("postgres_connection_factory_required")
    if not callable(authorize):
        raise Phase162Error("authorization_policy_required")
    return KDOPhase162Workflow(
        task_repository=PostgresEngineeringTaskRepository(connection_factory),
        workspace_repository=PostgresEngineeringWorkspaceRepository(connection_factory),
        source_repository_path=source_repository_path,
        managed_root=managed_root,
        authorize=authorize,
    )
