"""
GitHub Connector Plugin Admin Routes v2.0
Advanced GitHub intelligence and workflow automation endpoints.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Optional

from plugins.github_connector.routes.contracts import build_ci_payload

from core.security.deps import require_platform_or_species_privileges

router = APIRouter(
    prefix="/api/v1/github",
    tags=["github-connector"],
    dependencies=[Depends(require_platform_or_species_privileges)],
)


def _get_plugin():
    try:
        from plugins.github_connector.plugin import github_connector_plugin, init_github_connector_plugin
        if github_connector_plugin is None:
            return init_github_connector_plugin()
        return github_connector_plugin
    except Exception:
        return None


@router.get("/status")
async def get_status():
    plugin = _get_plugin()
    if not plugin:
        return {"status": "not_initialized", "token_configured": False, "version": "2.0.0"}
    return plugin.health_check()


@router.post("/config")
async def set_config(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
    token = data.get("token", "")
    if token:
        plugin.set_token(token)
    return {"ok": True, "token_configured": bool(plugin.github_token)}


@router.get("/repos")
async def list_repos(visibility: str = "all", sort: str = "updated", per_page: int = 30):
    plugin = _get_plugin()
    if not plugin or not plugin.github_token:
        raise HTTPException(status_code=503, detail={
            "code": "github_token_unavailable",
            "message": "GitHub repository inventory is unavailable because the connector token is not configured.",
        })

    data = await plugin._github_request(f"/user/repos?visibility={visibility}&sort={sort}&per_page={per_page}")
    if isinstance(data, list):
        return {"ok": True, "mock": False, "source": "github_api", "repositories": data, "count": len(data)}
    error = data.get("error", "Failed to fetch repositories") if isinstance(data, dict) else "GitHub returned an unrecognised repository response"
    raise HTTPException(status_code=502, detail={"code": "github_api_unavailable", "message": error})


@router.get("/repos/{owner}/{repo}/pulls")
async def list_pull_requests(owner: str, repo: str, state: str = "open"):
    plugin = _get_plugin()
    if not plugin or not plugin.github_token:
        raise HTTPException(status_code=503, detail={
            "code": "github_token_unavailable",
            "message": "GitHub pull-request inventory is unavailable because the connector token is not configured.",
        })

    data = await plugin._github_request(f"/repos/{owner}/{repo}/pulls?state={state}")
    if isinstance(data, list):
        return {"ok": True, "mock": False, "pull_requests": data}
    error = data.get("error", "Failed to fetch pull requests") if isinstance(data, dict) else "GitHub returned an unrecognised pull-request response"
    raise HTTPException(status_code=502, detail={"code": "github_api_unavailable", "message": error})


@router.get("/repos/{owner}/{repo}/issues")
async def list_issues(owner: str, repo: str, state: str = "open"):
    plugin = _get_plugin()
    if not plugin or not plugin.github_token:
        raise HTTPException(status_code=503, detail={
            "code": "github_token_unavailable",
            "message": "GitHub issue inventory is unavailable because the connector token is not configured.",
        })

    data = await plugin._github_request(f"/repos/{owner}/{repo}/issues?state={state}")
    if isinstance(data, list):
        return {"ok": True, "mock": False, "issues": data}
    error = data.get("error", "Failed to fetch issues") if isinstance(data, dict) else "GitHub returned an unrecognised issue response"
    raise HTTPException(status_code=502, detail={"code": "github_api_unavailable", "message": error})


@router.get("/ci")
async def list_ci_runs(max_repos: int = 10, per_repo: int = 5):
    """Return recent GitHub Actions runs across a bounded set of repositories.

    The endpoint is intentionally read-only and caps fan-out to protect the
    GitHub API rate budget. Missing credentials are reported as an explicit
    mock/unavailable state; no synthetic workflow runs are created.
    """
    plugin = _get_plugin()
    if not plugin or not plugin.github_token:
        raise HTTPException(status_code=503, detail={
            "code": "github_token_unavailable",
            "message": "GitHub workflow-run inventory is unavailable because the connector token is not configured.",
        })

    max_repos = max(1, min(int(max_repos), 20))
    per_repo = max(1, min(int(per_repo), 10))
    repositories = await plugin._github_request(
        f"/user/repos?sort=updated&per_page={max_repos}"
    )
    if not isinstance(repositories, list):
        error = repositories.get("error", "Failed to list repositories") if isinstance(repositories, dict) else "GitHub returned an unrecognised repository response"
        return {
            "ok": False,
            "mock": False,
            "source": "github_api",
            "runs": [],
            "count": 0,
            "repos_scanned": 0,
            "errors": [error],
            "error": error,
        }

    repositories = [
        repo for repo in repositories
        if isinstance(repo, dict) and isinstance(repo.get("full_name"), str) and repo["full_name"]
    ]
    responses = await asyncio.gather(
        *(
            plugin._github_request(
                f"/repos/{repo['full_name']}/actions/runs?per_page={per_repo}"
            )
            for repo in repositories
        ),
        return_exceptions=True,
    )
    return build_ci_payload(repositories, responses)


@router.post("/review-pr")
async def review_pr(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
    
    if not plugin.github_token:
        raise HTTPException(status_code=503, detail={
            "code": "github_token_unavailable",
            "message": "GitHub pull-request review is unavailable because the connector token is not configured.",
        })

    return plugin._handle_review_pr(data)


@router.post("/generate-description")
async def generate_description(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
        
    if not plugin.github_token:
        raise HTTPException(status_code=503, detail={
            "code": "github_token_unavailable",
            "message": "GitHub pull-request description generation is unavailable because the connector token is not configured.",
        })

    return plugin._handle_generate_pr_desc(data)


@router.post("/analyze-repo")
async def analyze_repo(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
        
    if not plugin.github_token:
        raise HTTPException(status_code=503, detail={
            "code": "github_token_unavailable",
            "message": "GitHub repository analysis is unavailable because the connector token is not configured.",
        })

    return plugin._handle_analyze_repo(data)


@router.post("/suggest-fix")
async def suggest_fix(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
        
    if not plugin.github_token:
        raise HTTPException(status_code=503, detail={
            "code": "github_token_unavailable",
            "message": "GitHub issue fix suggestions is unavailable because the connector token is not configured.",
        })

    return plugin._handle_suggest_fix(data)


@router.post("/generate-docs")
async def generate_docs(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
        
    if not plugin.github_token:
        raise HTTPException(status_code=503, detail={
            "code": "github_token_unavailable",
            "message": "GitHub repository documentation generation is unavailable because the connector token is not configured.",
        })

    return plugin._handle_generate_docs(data)


@router.post("/webhook-test")
async def webhook_test(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
    return plugin.handle_webhook(data)
