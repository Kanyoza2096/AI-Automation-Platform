"""Pure projections for GitHub Connector API response contracts."""

from __future__ import annotations

from typing import Any, Iterable


def build_ci_payload(repositories: Iterable[dict[str, Any]], responses: Iterable[Any]) -> dict[str, Any]:
    """Project GitHub Actions API responses into the KDO CI run contract."""
    runs: list[dict[str, Any]] = []
    errors: list[str] = []
    repos_scanned = 0

    for repo, response in zip(repositories, responses):
        full_name = str(repo.get("full_name") or "").strip()
        if not full_name:
            continue
        repos_scanned += 1

        if isinstance(response, BaseException):
            errors.append(f"{full_name}: {type(response).__name__}")
            continue

        if not isinstance(response, dict) or not isinstance(response.get("workflow_runs"), list):
            error = response.get("error") if isinstance(response, dict) else None
            errors.append(f"{full_name}: {error or 'unrecognised workflow response'}")
            continue

        for item in response["workflow_runs"]:
            if not isinstance(item, dict) or item.get("id") is None or not item.get("html_url"):
                continue
            runs.append({
                "repo": full_name,
                "id": item.get("id"),
                "name": item.get("name") or item.get("display_title") or "GitHub Actions run",
                "status": item.get("status") or "unknown",
                "conclusion": item.get("conclusion"),
                "branch": item.get("head_branch"),
                "event": item.get("event") or "unknown",
                "run_number": item.get("run_number"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "html_url": item.get("html_url"),
                "workflow_id": item.get("workflow_id"),
            })

    runs.sort(key=lambda run: str(run.get("updated_at") or ""), reverse=True)
    result = {
        "ok": True,
        "mock": False,
        "source": "github_api",
        "runs": runs,
        "count": len(runs),
        "repos_scanned": repos_scanned,
        "errors": errors,
    }
    if not runs and not errors:
        result["message"] = "No GitHub Actions runs were found in the scanned repositories."
    elif errors:
        result["message"] = "Some repository workflow runs could not be fetched."
    return result
