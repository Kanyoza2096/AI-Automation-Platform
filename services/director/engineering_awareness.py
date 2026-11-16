"""Read-only engineering change intelligence for the Director.

The service correlates canonical GitHub merge evidence with Render deployment
records. It deliberately does not infer deployment from a merge or operational
success from a deployment. Runtime acceptance remains a separate evidence gate.
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
MAX_RENDER_SERVICES = 30
MAX_DEPLOYS_PER_SERVICE = 10
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


def _unwrap_provider(value: Any, key: str) -> dict:
    if not isinstance(value, dict):
        return {}
    nested = value.get(key)
    return nested if isinstance(nested, dict) else value


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
            "deployment_evidence": [],
            "operational_verification_state": "unverified",
            "operational_verification_reason": "A runtime acceptance signal is required; merge and deployment records are insufficient.",
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


def _service_repository(service: dict) -> str | None:
    details = service.get("serviceDetails") or service.get("service_details") or {}
    candidate = (
        service.get("repo") or service.get("repoUrl") or service.get("repo_url")
        or (details.get("repo") if isinstance(details, dict) else None)
    )
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    return candidate.strip().lower().removesuffix(".git").rstrip("/")


def _configured_service_repositories() -> dict[str, str]:
    """Explicit mapping for Render services whose metadata omits repository URLs.

    Format: KDO_ENGINEERING_RENDER_SERVICE_MAP=service-id=owner/repo,...
    """
    result: dict[str, str] = {}
    for entry in os.getenv("KDO_ENGINEERING_RENDER_SERVICE_MAP", "").split(","):
        service_id, separator, repository = entry.strip().partition("=")
        if separator and service_id.strip() and repository.strip() in _repositories():
            result[service_id.strip()] = repository.strip()
    return result


def _deployment_commit(deploy: dict) -> str | None:
    commit = deploy.get("commit")
    if isinstance(commit, dict):
        value = commit.get("id") or commit.get("sha") or commit.get("commitId")
    else:
        value = commit
    value = value or deploy.get("commitId") or deploy.get("commit_id")
    return str(value).strip().lower() if value else None


def _deployment_status(deploy: dict) -> str:
    raw = str(deploy.get("status") or deploy.get("deployStatus") or "unknown").lower()
    if raw in {"live", "succeeded", "success"}:
        return "live"
    if raw in {"build_failed", "update_failed", "pre_deploy_failed", "failed", "canceled", "cancelled", "deactivated"}:
        return "failed"
    if raw in {"build_in_progress", "pre_deploy_in_progress", "update_in_progress", "created", "pending"}:
        return "in_progress"
    return raw or "unknown"


async def _load_render_evidence(repositories: list[str]) -> dict:
    if not os.getenv("RENDER_API_KEY", "").strip():
        return {
            "status": "unavailable",
            "source": "render_api",
            "message": "Render API credentials are not configured; deployment state remains unverified.",
            "services": [],
            "deployments": [],
        }

    try:
        from services.connectors.enterprise_registry import create_enterprise_adapter
        adapter = create_enterprise_adapter("render", allow_mutations=False)
        service_rows = await asyncio.to_thread(adapter.list_services, limit=MAX_RENDER_SERVICES)
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": "render_api",
            "message": f"Render service inventory unavailable ({type(exc).__name__}).",
            "services": [],
            "deployments": [],
        }

    if not isinstance(service_rows, list):
        service_rows = []
    configured_map = _configured_service_repositories()
    configured_ids = {
        item.strip() for item in os.getenv("KDO_ENGINEERING_RENDER_SERVICE_IDS", "").split(",")
        if item.strip()
    } | set(configured_map)
    normalized_services: list[dict] = []
    for row in service_rows:
        service = _unwrap_provider(row, "service")
        service_id = service.get("id")
        repo_url = _service_repository(service)
        repo_match = next(
            (repo for repo in repositories
             if repo.lower() in (repo_url or "")),
            None,
        )
        if str(service_id or "") in configured_map:
            repo_match = configured_map[str(service_id)]
        if configured_ids:
            if str(service_id or "") not in configured_ids:
                continue
        elif not repo_match:
            continue
        normalized_services.append({
            "service_id": str(service_id) if service_id else None,
            "name": service.get("name") or service.get("serviceName") or "Render service",
            "repository": repo_match,
            "repo_url": repo_url,
            "service_status": service.get("suspended") or service.get("status") or "unknown",
        })

    deploy_results = await asyncio.gather(
        *(
            asyncio.to_thread(adapter.list_deploys, item["service_id"], limit=MAX_DEPLOYS_PER_SERVICE)
            for item in normalized_services if item.get("service_id")
        ),
        return_exceptions=True,
    )
    deployments: list[dict] = []
    service_errors: list[str] = []
    service_items = [item for item in normalized_services if item.get("service_id")]
    for service, result in zip(service_items, deploy_results):
        if isinstance(result, Exception):
            service_errors.append(service["name"])
            continue
        rows = result if isinstance(result, list) else []
        for row in rows:
            deploy = _unwrap_provider(row, "deploy")
            deployments.append({
                "service_id": service["service_id"],
                "service_name": service["name"],
                "repository": service["repository"],
                "deploy_id": deploy.get("id"),
                "commit_sha": _deployment_commit(deploy),
                "status": _deployment_status(deploy),
                "created_at": deploy.get("createdAt") or deploy.get("created_at"),
                "finished_at": deploy.get("finishedAt") or deploy.get("finished_at"),
                "url": deploy.get("dashboardUrl") or deploy.get("url"),
                "evidence_source": "render_deployment",
            })

    render_status = "available" if normalized_services else ("unavailable" if service_errors else "unmatched")
    return {
        "status": render_status,
        "source": "render_api",
        "message": (
            "No Render services could be safely matched to the configured repositories. Configure KDO_ENGINEERING_RENDER_SERVICE_IDS if service metadata does not expose repository URLs."
            if not normalized_services else None
        ),
        "services": normalized_services,
        "deployments": deployments,
        "service_errors": service_errors,
    }


def _correlate_deployments(repositories: list[dict], deployments: list[dict]) -> None:
    for repository in repositories:
        repo_name = str(repository.get("repository") or "")
        repo_deployments = [
            item for item in deployments if item.get("repository") == repo_name
        ]
        for pr in repository.get("merged_pull_requests", []):
            candidates = {
                str(value).lower()
                for value in (pr.get("merge_commit_sha"), pr.get("head_sha"))
                if value
            }
            matches = [
                item for item in repo_deployments
                if item.get("commit_sha") and item["commit_sha"] in candidates
            ]
            pr["deployment_evidence"] = matches
            if any(item.get("status") == "live" for item in matches):
                pr["deployment_state"] = "deployed"
            elif any(item.get("status") == "in_progress" for item in matches):
                pr["deployment_state"] = "in_progress"
            elif any(item.get("status") == "failed" for item in matches):
                pr["deployment_state"] = "deployment_failed"
            else:
                pr["deployment_state"] = "unverified"


async def build_engineering_awareness(*, plugin: Any = None, now: datetime | None = None) -> dict:
    """Build a bounded evidence snapshot; every provider operation is read-only."""
    if plugin is None:
        from plugins.github_connector.plugin import (
            github_connector_plugin,
            init_github_connector_plugin,
        )
        plugin = github_connector_plugin or init_github_connector_plugin()

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    repositories = _repositories()
    authority = {
        "read_only": True,
        "plans_created": False,
        "tasks_created": False,
        "execution_authorized": False,
        "repository_mutations": False,
        "deployments_triggered": False,
    }
    if not getattr(plugin, "github_token", ""):
        return {
            "status": "unavailable",
            "source": "none",
            "observed_at": observed_at.isoformat(),
            "message": "GitHub connector token is not configured; no engineering changes were inferred.",
            "repositories": [],
            "render": {"status": "not_evaluated", "deployments": [], "services": []},
            "summary": {"repositories_configured": len(repositories), "repositories_available": 0, "merged_changes": 0, "deployed_changes": 0, "operationally_verified_changes": 0},
            "authority": authority,
        }

    if not repositories:
        return {
            "status": "unconfigured",
            "source": "none",
            "observed_at": observed_at.isoformat(),
            "message": "No engineering repositories are configured.",
            "repositories": [],
            "render": {"status": "not_evaluated", "deployments": [], "services": []},
            "summary": {"repositories_configured": 0, "repositories_available": 0, "merged_changes": 0, "deployed_changes": 0, "operationally_verified_changes": 0},
            "authority": authority,
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

    render = await _load_render_evidence(repositories)
    _correlate_deployments(normalized, render.get("deployments", []))
    available = sum(1 for item in normalized if item["status"] == "available")
    merged_count = sum(len(item["merged_pull_requests"]) for item in normalized)
    deployed_count = sum(
        1 for item in normalized for pr in item["merged_pull_requests"]
        if pr.get("deployment_state") == "deployed"
    )
    return {
        "status": "available" if available else "unavailable",
        "source": "github_api+render_api",
        "observed_at": observed_at.isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "repositories": normalized,
        "render": render,
        "summary": {
            "repositories_configured": len(repositories),
            "repositories_available": available,
            "merged_changes": merged_count,
            "render_services_matched": len(render.get("services", [])),
            "render_deployments_observed": len(render.get("deployments", [])),
            "deployed_changes": deployed_count,
            "operationally_verified_changes": 0,
        },
        "evidence_policy": {
            "implemented": "Repository contents or a commit establish source implementation; this snapshot lists merged pull requests only.",
            "merged": "A GitHub pull request with merged_at is authoritative merge evidence.",
            "deployed": "A Render deployment is correlated only when its commit SHA exactly matches the PR merge commit or head commit and Render reports the deployment live.",
            "operationally_verified": "Always unverified in this first slice. Requires a separate, capability-specific runtime acceptance signal.",
            "workflow": "The latest repository workflow is reported independently and is not attributed to a specific merge without exact commit correlation.",
        },
        "limitations": [
            "This is an on-demand snapshot, not yet a durable chronological engineering ledger.",
            "Render services without repository metadata require explicit KDO_ENGINEERING_RENDER_SERVICE_IDS configuration.",
            "Operational verification is deliberately not inferred from GitHub or Render status.",
        ],
        "authority": authority,
    }
