"""Phase 16.6 PostgreSQL workflow composition and atomic plan-review integration.

This module provides explicit production-style dependency composition. It does not
load credentials, create schemas, connect until a repository operation is invoked,
or grant merge/deployment/release authority.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from services.kdo_engineering_phase16_2 import KDOPhase162Workflow
from services.kdo_engineering_phase16_5 import (
    PostgresEngineeringTaskRepository,
    PostgresEngineeringWorkspaceRepository,
)


def create_postgres_engineering_workflow(*, connection_factory: Callable[[], Any],
        source_repository_path: str | Path, managed_root: str | Path,
        authorize: Callable[[str, str, str, str], bool]) -> KDOPhase162Workflow:
    """Compose Phase 16.2 workflow with shared PostgreSQL task/workspace adapters.

    The caller must provide a server-side connection factory configured for the
    canonical PostgreSQL database and a real authorization policy. No SQLite fallback
    or implicit configuration discovery is performed.
    """
    if not callable(connection_factory):
        raise ValueError("postgres_connection_factory_required")
    if not callable(authorize):
        raise ValueError("authorization_policy_required")
    return KDOPhase162Workflow(
        task_repository=PostgresEngineeringTaskRepository(connection_factory),
        workspace_repository=PostgresEngineeringWorkspaceRepository(connection_factory),
        source_repository_path=source_repository_path,
        managed_root=managed_root,
        authorize=authorize,
    )
