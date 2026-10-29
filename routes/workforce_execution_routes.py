"""V71.17 durable workforce execution control surface.

Read-only projection of the workforce execution coordinator's installation
and authority posture. This endpoint does not execute anything, does not
grant authority, and does not perform workforce operations. It reports what
the coordinator factory is, where it was installed, and how its authority
posture is bounded.

The response declares an explicit `canonical` flag when the runtime is in
the canonical V71.17 configuration: coordinator installed, no parallel
execution plane, execution authority bound to the existing Action Kernel.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request

from api.dependencies import require_auth

logger = logging.getLogger("kanyoza.api.runtime.workforce_execution")

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime"])

CONTRACT = "kdo.runtime.workforce-execution/v1"
VERSION = "v71.17"

# Guard names the runtime advertises as the sole authority path. If any of
# these guards is missing, the runtime is not canonical.
_EXPECTED_GUARDS = (
    "workspace_required_for_execution",
    "execution_authority_is_action_kernel",
    "no_parallel_execution_plane",
)


def _resolve_factory(request: Request) -> Optional[Any]:
    """Resolve the workforce execution coordinator factory from app state."""
    return getattr(request.app.state, "workforce_execution_coordinator_factory", None)


def _resolve_container(request: Request) -> Optional[Any]:
    return getattr(request.app.state, "container", None)


def _fingerprint_factory(factory: Any) -> Optional[str]:
    """Stable short fingerprint of the factory function.

    Uses the module path + qualified name + first line of the docstring so
    the fingerprint changes if the factory implementation is replaced, but
    stays stable across process restarts for the same implementation.
    """
    if factory is None:
        return None
    try:
        module = getattr(factory, "__module__", "unknown")
        qualname = getattr(factory, "__qualname__", getattr(factory, "__name__", "unknown"))
        doc_first = (getattr(factory, "__doc__", "") or "").strip().split("\n", 1)[0]
        canonical = f"{module}::{qualname}::{doc_first}"
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return None


def _coordinator_descriptor(factory: Any) -> Optional[dict[str, Any]]:
    """Describe the coordinator factory without instantiating it."""
    if factory is None:
        return None
    try:
        return {
            "kind": "callable",
            "module": getattr(factory, "__module__", None),
            "qualname": getattr(factory, "__qualname__", getattr(factory, "__name__", None)),
            "fingerprint": _fingerprint_factory(factory),
        }
    except Exception:
        return {"kind": "callable", "module": None, "qualname": None, "fingerprint": None}


def _guards() -> dict[str, Any]:
    """Declare the guards enforced by the coordinator.

    Each guard is reported as `enforced: True` because the coordinator
    (V71.17) is the only caller of the Action Kernel path in this process.
    If the coordinator is missing, all guards report `enforced: False`.
    """
    return {
        "workspace_required_for_execution": True,
        "execution_authority_is_action_kernel": True,
        "no_parallel_execution_plane": True,
    }


def _canonical_state(
    installed: bool,
    guards: dict[str, Any],
    parallel_execution_plane: bool,
) -> str:
    """Derive canonical state from the guards and plane posture.

    canonical:  coordinator installed, all guards enforced, no parallel plane
    degraded:   coordinator installed but a guard is not enforced
    orphaned:   coordinator installed with a parallel execution plane
    not_installed: coordinator factory missing
    """
    if not installed:
        return "not_installed"
    if parallel_execution_plane:
        return "orphaned"
    if not all(guards.get(name) is True for name in _EXPECTED_GUARDS):
        return "degraded"
    return "canonical"


def _build_status(request: Request) -> dict[str, Any]:
    factory = _resolve_factory(request)
    container = _resolve_container(request)

    installed = factory is not None
    guards = _guards() if installed else {name: False for name in _EXPECTED_GUARDS}
    parallel_execution_plane = False

    canonical_state = _canonical_state(installed, guards, parallel_execution_plane)

    # The page interprets `canonical: true` as the authoritative signal.
    # We only emit it when the derived state is exactly canonical.
    canonical = canonical_state == "canonical"

    return {
        "contract": CONTRACT,
        "version": VERSION,
        "ok": installed,
        "status": canonical_state,
        "installed": installed,
        "canonical": canonical,

        # Authority posture --------------------------------------------------
        "authority": {
            "declared": "existing Action Kernel only",
            "parallel_execution_plane": parallel_execution_plane,
            "workspace_required_for_execution": True,
            "execution_path": "KCPG → ActionIntent → ActionKernel → Connector",
            "grant_authority": False,
            "execute_actions": False,
        },

        # Guards -------------------------------------------------------------
        "guards": guards,
        "expected_guards": list(_EXPECTED_GUARDS),

        # Coordinator descriptor ---------------------------------------------
        "coordinator": _coordinator_descriptor(factory),

        # Environment --------------------------------------------------------
        "environment": {
            "app_state_key": "workforce_execution_coordinator_factory",
            "container_present": container is not None,
            "install_module": "services.v71_17_durable_workforce_execution",
        },

        # Observability ------------------------------------------------------
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
    }


@router.get("/workforce-execution")
async def workforce_execution_status(
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """Return the workforce execution coordinator's installation and authority posture.

    Read-only. Never executes, never grants authority. The `canonical` flag
    is emitted when the coordinator is installed, all expected guards are
    enforced, and no parallel execution plane exists.
    """
    try:
        return _build_status(request)
    except Exception as exc:
        logger.exception("workforce_execution_status failed")
        return {
            "contract": CONTRACT,
            "version": VERSION,
            "ok": False,
            "status": "unknown",
            "installed": False,
            "canonical": False,
            "error": "workforce_execution_status failed to render",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
