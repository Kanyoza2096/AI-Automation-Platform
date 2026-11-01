"""Remaining Control Plane gap endpoints (thin but real HTTP surface)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from core.security.deps import require_species_privileges

logger = logging.getLogger("kanyoza.routes.cp_gaps")
router = APIRouter(
    prefix="/api/v1",
    tags=["control-plane-gaps"],
    dependencies=[Depends(require_species_privileges)],
)


class QualityBody(BaseModel):
    content: str = Field(default="", max_length=20000)


# ═══════════════════════════════════════════════════════════════════════════
# EXISTING ROUTES
# Truth policy: never invent "nominal" / fixed topology / heuristic scores.
# Prefer explicit unavailable + source: "none" until a real engine is bound.
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/alerts")
async def alerts():
    try:
        from services.meta_engine import meta_engine
        return {"ok": True, "alerts": meta_engine.get_alerts(), "source": "meta_engine"}
    except Exception as exc:
        return {"ok": False, "alerts": [], "source": "unavailable", "error": type(exc).__name__}


@router.get("/rules")
async def rules():
    return {"ok": True, "rules": [], "source": "none"}




@router.get("/condition/health")
async def condition_health():
    try:
        from services import condition_engine as ce_mod
        engine = getattr(ce_mod, "autonomous_engine", None)
        if engine is not None and hasattr(engine, "get_status"):
            value = engine.get_status()
            if isinstance(value, dict):
                return {
                    "ok": True,
                    "status": str(value.get("status", "unknown")).lower(),
                    "source": "condition_engine",
                    "conditions": value.get("conditions") or value.get("active") or [],
                    **{k: v for k, v in value.items() if k not in ("status", "conditions", "active")},
                }
        if hasattr(ce_mod, "get_engine_status"):
            value = ce_mod.get_engine_status()
            if isinstance(value, dict):
                return {
                    "ok": True,
                    "status": str(value.get("status", "unknown")).lower(),
                    "source": "condition_engine",
                    "conditions": value.get("conditions") or [],
                }
        if hasattr(ce_mod, "health_check"):
            value = ce_mod.health_check()
            if isinstance(value, dict):
                return {"ok": True, "status": str(value.get("status", "unknown")).lower(), "source": "condition_engine", "conditions": []}
    except Exception as exc:
        logger.warning("condition health unbound: %s", type(exc).__name__)
    return {
        "ok": False,
        "status": "unavailable",
        "source": "none",
        "conditions": [],
        "message": "No authoritative condition health provider is bound to this compatibility surface.",
    }






@router.get("/quality/posts")
async def quality_posts():
    return {"ok": True, "posts": [], "source": "none"}




@router.post("/quality/analyze")
async def quality_analyze(body: QualityBody):
    # Heuristic length-based scoring is disabled on the control-plane surface.
    return {
        "ok": False,
        "status": "unavailable",
        "source": "none",
        "score": None,
        "message": "Quality analysis is not bound; heuristic scoring is disabled on the control-plane surface.",
    }








@router.get("/applications")
async def applications():
    return {"ok": True, "applications": [], "source": "none"}


@router.get("/repos")
async def get_repos():
    """List GitHub repositories visible to the platform operator token.

    Sources in order:
      1. GitHub adapter (requires GITHUB_TOKEN env var; reads /user/repos)
      2. Empty list with honest source="none" and an explicit reason

    Workspace-scoped registrations (github_repos table) are not yet
    implemented; when they are, they should be checked before the adapter.
    """
    try:
        from services.connectors.github_adapter import GitHubAdapter
        from services.connectors.enterprise_provider_base import ProviderAdapterError

        adapter = GitHubAdapter()

        # The base adapter exposes the token as `_token` (or `token` depending
        # on the base class implementation). Check both to be defensive.
        token = getattr(adapter, "_token", None)
        if token is None:
            token = getattr(adapter, "token", "")

        if not token:
            logger.debug("get_repos: GITHUB_TOKEN not configured")
            return {
                "ok": True,
                "repos": [],
                "source": "none",
                "message": "GITHUB_TOKEN not configured",
            }

        repos = adapter.list_repos(per_page=100)
        if repos:
            return {
                "ok": True,
                "repos": repos,
                "count": len(repos),
                "source": "github_adapter",
            }
        return {
            "ok": True,
            "repos": [],
            "source": "none",
            "message": "Token configured but no repos returned",
        }
    except ProviderAdapterError as exc:
        logger.warning("get_repos: GitHub adapter error: %s", exc)
        return {
            "ok": False,
            "repos": [],
            "source": "github_adapter_error",
            "error": str(exc),
        }
    except Exception as exc:
        logger.exception("get_repos: unexpected error")
        return {
            "ok": False,
            "repos": [],
            "source": "error",
            "error": type(exc).__name__,
        }

@router.get("/github/ci")
async def get_ci_runs(repo: Optional[str] = None, limit: int = 30):
    """List recent GitHub Actions workflow runs.

    If `repo` is provided as "owner/name", fetches runs for that repo only.
    Otherwise scans up to 10 repos (most recently pushed) and aggregates the
    most recent run per repo, bounded to `limit` total runs.

    Bounded to avoid GitHub rate-limit exhaustion (5000 req/hr authenticated).
    Read-only; does not dispatch or modify workflows.
    """
    try:
        from services.connectors.github_adapter import GitHubAdapter
        from services.connectors.enterprise_provider_base import ProviderAdapterError

        adapter = GitHubAdapter()
        token = getattr(adapter, "_token", None) or getattr(adapter, "token", "")
        if not token:
            return {
                "ok": True,
                "runs": [],
                "source": "none",
                "message": "GITHUB_TOKEN not configured",
            }

        # Determine which repos to scan.
        targets: list[tuple[str, str]] = []
        if repo and "/" in repo:
            owner, name = repo.split("/", 1)
            targets = [(owner.strip(), name.strip())]
        else:
            try:
                all_repos = adapter.list_repos(per_page=30, sort="pushed", direction="desc")
            except ProviderAdapterError as exc:
                return {
                    "ok": False,
                    "runs": [],
                    "source": "github_adapter_error",
                    "error": str(exc),
                }
            for r in all_repos[:10]:
                owner = (r.get("owner") or {}).get("login")
                name = r.get("name")
                if owner and name:
                    targets.append((owner, name))

        runs: list[dict[str, Any]] = []
        per_repo_limit = max(1, min(limit // max(len(targets), 1) + 2, 10))
        errors: list[str] = []

        for owner, name in targets:
            try:
                repo_runs = adapter.list_workflow_runs(
                    owner, name, per_page=per_repo_limit
                )
                for run in repo_runs:
                    runs.append({
                        "repo": f"{owner}/{name}",
                        "id": run.get("id"),
                        "name": run.get("name") or run.get("display_title") or f"Run #{run.get('id')}",
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                        "branch": run.get("head_branch"),
                        "event": run.get("event"),
                        "run_number": run.get("run_number"),
                        "created_at": run.get("created_at"),
                        "updated_at": run.get("updated_at"),
                        "html_url": run.get("html_url"),
                        "workflow_id": run.get("workflow_id"),
                    })
            except ProviderAdapterError as exc:
                errors.append(f"{owner}/{name}: {exc}")
            except Exception as exc:
                errors.append(f"{owner}/{name}: {type(exc).__name__}")

        # Sort: in-progress first, then most recent
        def sort_key(r: dict) -> tuple:
            status = r.get("status") or ""
            in_progress = status in ("in_progress", "queued", "waiting", "pending")
            ts = r.get("updated_at") or r.get("created_at") or ""
            return (0 if in_progress else 1, ts)

        runs.sort(key=sort_key)
        runs = runs[:limit]

        return {
            "ok": True,
            "runs": runs,
            "count": len(runs),
            "repos_scanned": len(targets),
            "source": "github_adapter",
            "errors": errors if errors else None,
        }
    except Exception as exc:
        logger.exception("get_ci_runs failed")
        return {
            "ok": False,
            "runs": [],
            "source": "error",
            "error": type(exc).__name__,
        }












# ═══════════════════════════════════════════════════════════════════════════
# FRONTEND POLLING ENDPOINTS (avoid 404s, stay truthful)
# ═══════════════════════════════════════════════════════════════════════════











def register_cp_gap_routes(app) -> None:
    app.include_router(router)
