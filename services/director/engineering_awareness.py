"""Read-only engineering change intelligence for the Director.

This first slice observes canonical GitHub evidence only. It does not create
plans, tasks, approvals, deployments, commits, or execution authority.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_REPOSITORIES = (
    "Kanyoza2096/AI-Automation-Platform",
    "Kanyoza2096/KDO-Control-plane-",
    "Kanyoza2096/KCPG-Gateway",
)
MAX_REPOSITORIES = 10
MAX_MERGED_PRS_PER_REPOSITORY = 15
LOOKBACK_DAYS = 30


def _repositories() -> list[str]:
    configured = os.getenv("KDO_ENGINEERING_REPOSITORIES", "")
    values = configured.split(",") if configured.strip() else DEFAULT_REPOSITORIES
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        repo = str(value).strip()
        if not repo or "/" not in repo or repo.startswith("/") or repo.endswith("/"):
            continue
        if repo not in seen:
            seen.add(repo)
            result.append(repo)
        if len(result) >= MAX_REPOSITORIES:
            break
    return result


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and bool(value.get("error"))


def _workflow_summary(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    runs = value.get("workflow_runs")
    if not isinstance(runs, list) or not runs:
        return None
    run = runs[0]
    if not isinstance(run, dict):
        return None
    return {
        "name": run.get("name") or run.get("display_title") or "Workflow",
        "status": run.get("status") or "unknown",
        "conclusion": run.get("conclusion"),
        "head_sha": run.get("head_sha"),
        "updated_at": run.get("updated_at"),
        "url": run.get("html_url"),
        "source": "github_actions",
        "correlated_to_merge": False,
    }


async def _load_repository(plugin: Any, full_name: str, cutoff: datetime) -> dict:
    pulls_path = (
        f"/repos/{full_name}/pulls?state=closed&per_page=50"
        "&sort=updated&direction=desc"
    )
    pulls = await plugin._github_request(pulls_path)
    if _is_error(pulls) or not isinstance(pulls, list):
        message = pulls.get("error", "Unrecognised GitHub response") if isinstance(pulls, dict) else "Unrecognised GitHub response"
        return {
            "repository": full_name,
            "status": "unavailable",
            "error": str(message)[:240],
            "merged_pull_requests": [],
            "latest_workflow": None,
        }

    merged: list[dict] = []
    for pr in pulls:
        if not isinstance(pr, dict) or not pr.get("merged_at"):
            continue
        try:
            merged_at = datetime.fromisoformat(str(pr["merged_at"]).replace("Z", "+00:00"))
            if merged_at.tzinfo is None:
                merged_at = merged_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if merged_at < cutoff:
            continue
        merged.append({
            "number": pr.get("number"),
            "title": str(pr.get("title") or "Untitled pull request")[:300],
            "body": str(pr.get("body") or "")[:1200],
            "state": "merged",
            "merged_at": merged_at.astimezone(timezone.utc).isoformat(),
            "merge_commit_sha": pr.get("merge_commit_sha"),
            "head_sha": (pr.get("head") or {}).get("sha") if isinstance(pr.get("head"), dict) else None,
            "base_branch": (pr.get("base") or {}).get("ref") if isinstance(pr.get("base"), dict) else None,
            "changed_files": pr.get("changed_files"),
            "additions": pr.get("additions"),
            "deletions": pr.get("deletions"),
            "url": pr.get("html_url"),
            "evidence_source": "github_pull_request",
            "implementation_state": "merged",
            "deployment_state": "unverified",
            "operational_verification_state": "unverified",
        })
        if len(merged) >= MAX_MERGED_PRS_PER_REPOSITORY:
            break

    workflow_value = await plugin._github_request(
        f"/repos/{full_name}/actions/runs?per_page=1"
    )
    return {
        "repository": full_name,
        "status": "available",
        "merged_pull_requests": merged,
        "latest_workflow": _workflow_summary(workflow_value),
        "evidence_source": "github_api",
    }


async def build_engineering_awareness(*, plugin: Any = None, now: datetime | None = None) -> dict:
    """Build a bounded snapshot from GitHub; never mutates external state."""
    if plugin is None:
        from plugins.github_connector.plugin import (
            github_connector_plugin,
            init_github_connector_plugin,
        )
        plugin = github_connector_plugin or init_github_connector_plugin()

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    repositories = _repositories()
    if not getattr(plugin, "github_token", ""):
        return {
            "status": "unavailable",
            "source": "none",
            "observed_at": observed_at.isoformat(),
            "message": "GitHub connector token is not configured; no engineering changes were inferred.",
            "repositories": [],
            "summary": {"repositories_configured": len(repositories), "repositories_available": 0, "merged_changes": 0},
            "evidence_policy": {
                "merged": "A GitHub pull request with merged_at is implementation evidence.",
                "deployed": "Not established by this source.",
                "operationally_verified": "Not established by this source.",
            },
            "authority": {"read_only": True, "plans_created": False, "tasks_created": False, "execution_authorized": False},
        }

    if not repositories:
        return {
            "status": "unconfigured",
            "source": "none",
            "observed_at": observed_at.isoformat(),
            "message": "No engineering repositories are configured.",
            "repositories": [],
            "summary": {"repositories_configured": 0, "repositories_available": 0, "merged_changes": 0},
            "authority": {"read_only": True, "plans_created": False, "tasks_created": False, "execution_authorized": False},
        }

    cutoff = observed_at - timedelta(days=LOOKBACK_DAYS)
    results = await asyncio.gather(
        *(_load_repository(plugin, repo, cutoff) for repo in repositories),
        return_exceptions=True,
    )
    normalized: list[dict] = []
    for repo, result in zip(repositories, results):
        if isinstance(result, Exception):
            normalized.append({
                "repository": repo,
                "status": "unavailable",
                "error": type(result).__name__,
                "merged_pull_requests": [],
                "latest_workflow": None,
            })
        else:
            normalized.append(result)

    available = sum(1 for item in normalized if item["status"] == "available")
    merged_count = sum(len(item["merged_pull_requests"]) for item in normalized)
    return {
        "status": "available" if available else "unavailable",
        "source": "github_api",
        "observed_at": observed_at.isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "repositories": normalized,
        "summary": {
            "repositories_configured": len(repositories),
            "repositories_available": available,
            "merged_changes": merged_count,
        },
        "evidence_policy": {
            "merged": "A GitHub pull request with merged_at is implementation evidence.",
            "deployed": "Unverified. A GitHub merge or workflow run is not proof of a Render deployment.",
            "operationally_verified": "Unverified. Requires a separate runtime acceptance signal.",
            "workflow": "Latest repository workflow is reported independently and is not attributed to a specific merge unless commit identity is independently matched.",
        },
        "authority": {
            "read_only": True,
            "plans_created": False,
            "tasks_created": False,
            "execution_authorized": False,
        },
    }
