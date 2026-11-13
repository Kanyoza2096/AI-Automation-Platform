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
        # Provide rich mock repositories for testing if no token is configured
        return {
            "ok": True,
            "mock": True,
            "repositories": [
                {
                    "id": 101,
                    "name": "AI-Automation-Platform",
                    "full_name": "kanyoza/AI-Automation-Platform",
                    "description": "Autonomous multi-agent orchestration & condition engine",
                    "private": True,
                    "html_url": "https://github.com/kanyoza/AI-Automation-Platform",
                    "language": "Python",
                    "stargazers_count": 142,
                    "forks_count": 28,
                    "open_issues_count": 5,
                    "default_branch": "main",
                    "updated_at": "2026-08-19T10:45:00Z"
                },
                {
                    "id": 102,
                    "name": "Mutex-Mission-Control",
                    "full_name": "kanyoza/Mutex-Mission-Control",
                    "description": "High-density glassmorphism control plane frontend",
                    "private": True,
                    "html_url": "https://github.com/kanyoza/Mutex-Mission-Control",
                    "language": "TypeScript",
                    "stargazers_count": 89,
                    "forks_count": 12,
                    "open_issues_count": 2,
                    "default_branch": "main",
                    "updated_at": "2026-08-19T11:15:00Z"
                },
                {
                    "id": 103,
                    "name": "Trend-Intelligence-SDK",
                    "full_name": "kanyoza/Trend-Intelligence-SDK",
                    "description": "Predictive signal processing & social trend analyzer",
                    "private": False,
                    "html_url": "https://github.com/kanyoza/Trend-Intelligence-SDK",
                    "language": "Python",
                    "stargazers_count": 340,
                    "forks_count": 45,
                    "open_issues_count": 8,
                    "default_branch": "main",
                    "updated_at": "2026-08-18T18:30:00Z"
                }
            ]
        }
    
    data = await plugin._github_request(f"/user/repos?visibility={visibility}&sort={sort}&per_page={per_page}")
    if isinstance(data, list):
        return {"ok": True, "mock": False, "source": "github_api", "repositories": data, "count": len(data)}
    error = data.get("error", "Failed to fetch repositories") if isinstance(data, dict) else "GitHub returned an unrecognised repository response"
    return {"ok": False, "mock": False, "repositories": [], "count": 0, "error": error}


@router.get("/repos/{owner}/{repo}/pulls")
async def list_pull_requests(owner: str, repo: str, state: str = "open"):
    plugin = _get_plugin()
    if not plugin or not plugin.github_token:
        return {
            "ok": True,
            "mock": True,
            "pull_requests": [
                {
                    "number": 42,
                    "title": "feat: Add autonomous condition branch evaluation",
                    "user": {"login": "alex-dev", "avatar_url": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=64"},
                    "state": "open",
                    "created_at": "2026-08-19T08:30:00Z",
                    "updated_at": "2026-08-19T09:12:00Z",
                    "html_url": f"https://github.com/{owner}/{repo}/pull/42",
                    "head": {"ref": "feature/condition-branches"},
                    "base": {"ref": "main"},
                    "body": "Implements BFS graph resolution for nested rule triggers."
                },
                {
                    "number": 41,
                    "title": "fix: Harden rate limit timestamp parsing in Supabase adapter",
                    "user": {"login": "sarah-sec", "avatar_url": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=64"},
                    "state": "open",
                    "created_at": "2026-08-18T20:10:00Z",
                    "updated_at": "2026-08-18T22:05:00Z",
                    "html_url": f"https://github.com/{owner}/{repo}/pull/41",
                    "head": {"ref": "fix/timestamp-iso"},
                    "base": {"ref": "main"},
                    "body": "Prevents literal now() sql string injection on rate limiter updates."
                }
            ]
        }
    
    data = await plugin._github_request(f"/repos/{owner}/{repo}/pulls?state={state}")
    if isinstance(data, list):
        return {"ok": True, "mock": False, "pull_requests": data}
    error = data.get("error", "Failed to fetch pull requests") if isinstance(data, dict) else "GitHub returned an unrecognised pull-request response"
    return {"ok": False, "mock": False, "pull_requests": [], "error": error}


@router.get("/repos/{owner}/{repo}/issues")
async def list_issues(owner: str, repo: str, state: str = "open"):
    plugin = _get_plugin()
    if not plugin or not plugin.github_token:
        return {
            "ok": True,
            "mock": True,
            "issues": [
                {
                    "number": 18,
                    "title": "SSE connection dropped during heavy log volume bursts",
                    "user": {"login": "devops-lead", "avatar_url": "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=64"},
                    "state": "open",
                    "created_at": "2026-08-19T06:20:00Z",
                    "comments": 3,
                    "html_url": f"https://github.com/{owner}/{repo}/issues/18",
                    "body": "Under 10k eps, client SSE buffers stall before heartbeat timeout."
                },
                {
                    "number": 19,
                    "title": "Support query-based JWT authentication in event stream endpoint",
                    "user": {"login": "watson-ai", "avatar_url": "https://images.unsplash.com/photo-1492562080023-ab3db95bfbce?w=64"},
                    "state": "open",
                    "created_at": "2026-08-19T07:15:00Z",
                    "comments": 1,
                    "html_url": f"https://github.com/{owner}/{repo}/issues/19",
                    "body": "Browser EventSource cannot set custom headers, need ?token= param support."
                }
            ]
        }
    
    data = await plugin._github_request(f"/repos/{owner}/{repo}/issues?state={state}")
    if isinstance(data, list):
        return {"ok": True, "mock": False, "issues": data}
    error = data.get("error", "Failed to fetch issues") if isinstance(data, dict) else "GitHub returned an unrecognised issue response"
    return {"ok": False, "mock": False, "issues": [], "error": error}


@router.get("/ci")
async def list_ci_runs(max_repos: int = 10, per_repo: int = 5):
    """Return recent GitHub Actions runs across a bounded set of repositories.

    The endpoint is intentionally read-only and caps fan-out to protect the
    GitHub API rate budget. Missing credentials are reported as an explicit
    mock/unavailable state; no synthetic workflow runs are created.
    """
    plugin = _get_plugin()
    if not plugin or not plugin.github_token:
        return {
            "ok": True,
            "mock": True,
            "source": "mock",
            "runs": [],
            "count": 0,
            "repos_scanned": 0,
            "message": "GitHub token not configured; workflow runs are unavailable.",
        }

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
    
    # If no token configured or mock testing requested, return realistic AI code review
    if not plugin.github_token:
        repo = data.get("repo", "kanyoza/AI-Automation-Platform")
        pr_number = data.get("pr_number", 42)
        review = (
            f"### 🤖 AI Code Intelligence Review for PR #{pr_number}\n\n"
            f"**Repository:** `{repo}`\n\n"
            "#### 🔍 Summary of Changes\n"
            "- Added BFS resolution graph in rule condition runner.\n"
            "- Introduced circuit-breaker state machine with 3-strike policy.\n"
            "- Optimized memory allocation in SSE stream serializer.\n\n"
            "#### ✅ Strengths\n"
            "1. **Thread-Safety:** Lock contention reduced by 40% using double-checked locking.\n"
            "2. **Defensive Validation:** Proper boundary checks on input payload sizes.\n\n"
            "#### ⚠️ Recommendations & Bug Catches\n"
            "- **Line 114:** `datetime.now()` lacks timezone awareness. Recommend `datetime.now(timezone.utc)`.\n"
            "- **Line 240:** Ensure `AbortSignal.any` has fallback polyfill for older runtime compatibility.\n\n"
            "**Verdict:** `APPROVED WITH MINOR SUGGESTIONS` ✨"
        )
        return {"ok": True, "mock": True, "review": review}
        
    return plugin._handle_review_pr(data)


@router.post("/generate-description")
async def generate_description(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
        
    if not plugin.github_token:
        pr_number = data.get("pr_number", 42)
        desc = (
            f"## Description for Pull Request #{pr_number}\n\n"
            "### 🎯 Objectives\n"
            "This pull request refactors the condition evaluation engine to execute non-blocking parallel branch evaluations with integrated telemetry.\n\n"
            "### 🛠️ Key Changes\n"
            "- Implemented BFS resolution queue for nested trigger chains.\n"
            "- Added rate-limiting sliding window guard.\n"
            "- Added unit tests covering circular dependency edge cases.\n\n"
            "### 🧪 Verification\n"
            "- [x] Unit test suite passed (`pytest tests/ -v`)\n"
            "- [x] Stress benchmark verified at 5,000 req/s\n"
            "- [x] Security lint passed with zero CVE findings"
        )
        return {"ok": True, "mock": True, "description": desc}
        
    return plugin._handle_generate_pr_desc(data)


@router.post("/analyze-repo")
async def analyze_repo(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
        
    if not plugin.github_token:
        repo = data.get("repo", "kanyoza/AI-Automation-Platform")
        analysis = (
            f"## 🏛️ Architectural Health & Code Quality Report\n\n"
            f"**Repository:** `{repo}`\n"
            f"**Health Score:** `94/100` (Grade: A)\n\n"
            "### 📊 Key Metrics\n"
            "- **Modularity:** High — Clean domain layer separation between services, repositories, and routers.\n"
            "- **Error Resilience:** Excellent — Comprehensive `_safe_err` boundaries and retry decorators.\n"
            "- **Security Posture:** OWASP Top 10 hardened with strict CORS, HMAC secret comparison, and regex prompt shielding.\n"
            "- **Observability:** Integrated Prometheus counters, SSE log streaming, and Socket.IO heartbeat channels.\n\n"
            "### 🚀 Optimization Opportunities\n"
            "1. Migrate synchronous database table introspection to cached schema maps.\n"
            "2. Implement distributed Redis locking for multi-instance scheduler cluster instances."
        )
        return {"ok": True, "mock": True, "analysis": analysis}
        
    return plugin._handle_analyze_repo(data)


@router.post("/suggest-fix")
async def suggest_fix(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
        
    if not plugin.github_token:
        issue_number = data.get("issue_number", 18)
        fix = (
            f"## 🛠️ Automated Fix Proposal for Issue #{issue_number}\n\n"
            "### Root Cause\n"
            "The client-side `EventSource` drops because the HTTP proxy buffers output until a chunk boundary is reached without periodic keepalive comments.\n\n"
            "### Proposed Code Solution\n"
            "```python\n"
            "# api/v1.py - Add SSE keepalive heartbeat ping\n"
            "async def event_generator():\n"
            "    while True:\n"
            "        if await queue.empty():\n"
            "            yield ': ping\\n\\n'  # Keepalive comment prevents proxy timeout\n"
            "            await asyncio.sleep(15)\n"
            "        else:\n"
            "            item = await queue.get()\n"
            "            yield f'data: {json.dumps(item)}\\n\\n'\n"
            "```\n\n"
            "### Impact Assessment\n"
            "- **Complexity:** Minimal (4 lines changed)\n"
            "- **Risk:** None (standard SSE protocol behavior)"
        )
        return {"ok": True, "mock": True, "suggestion": fix}
        
    return plugin._handle_suggest_fix(data)


@router.post("/generate-docs")
async def generate_docs(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
        
    if not plugin.github_token:
        file_path = data.get("file_path", "core/condition_engine.py")
        docs = (
            f"# Technical Documentation: `{file_path}`\n\n"
            "## Overview\n"
            "The `ConditionEngine` provides high-throughput rule evaluation for incoming event signals across the platform.\n\n"
            "## Architecture\n"
            "```mermaid\n"
            "graph TD\n"
            "  Event[Incoming Event Signal] --> Parser[Event Normalizer]\n"
            "  Parser --> Evaluator[Predicate Engine]\n"
            "  Evaluator --> Action[Action Dispatcher]\n"
            "  Action --> Bus[Event Bus]\n"
            "```\n\n"
            "## Core Classes\n"
            "### `ConditionEngine`\n"
            "- `evaluate(event: dict) -> EvaluationResult`: Evaluates rules in O(N) time with memoization.\n"
            "- `register_rule(rule: RuleDefinition) -> str`: Dynamic rule injection with validation."
        )
        return {"ok": True, "mock": True, "documentation": docs}
        
    return plugin._handle_generate_docs(data)


@router.post("/webhook-test")
async def webhook_test(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
    return plugin.handle_webhook(data)
