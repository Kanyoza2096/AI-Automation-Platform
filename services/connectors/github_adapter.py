"""GitHub enterprise adapter for governed repository and CI/CD operations.

Read operations are available by default. Mutations require allow_mutations=True;
high-risk workflow dispatch, workflow-run control, and PR merges also require
allow_high_risk=True and confirm=True at the call site.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional
import httpx
from services.connectors.enterprise_provider_base import EnterpriseProviderAdapter, ProviderAdapterError


class GitHubAdapter(EnterpriseProviderAdapter):
    provider = "github"
    API_BASE = "https://api.github.com"

    def __init__(self, *, token: Optional[str] = None, base_url: str = API_BASE,
                 client: Optional[httpx.Client] = None, timeout: float = 20.0,
                 allow_mutations: bool = False, allow_high_risk: bool = False):
        super().__init__(token=token or os.getenv("GITHUB_TOKEN", ""), base_url=base_url,
                         client=client, timeout=timeout, allow_mutations=allow_mutations,
                         allow_high_risk=allow_high_risk)

    def _headers(self):
        h = super()._headers()
        h.update({"X-GitHub-Api-Version": "2022-11-28"})
        return h

    @staticmethod
    def _repo(owner: str, repo: str) -> str:
        if not owner or not repo or "/" in owner or "/" in repo:
            raise ValueError("owner and repo must be non-empty path segments")
        return f"repos/{owner}/{repo}"

    def health(self) -> dict[str, Any]:
        code, data, headers = self._request("GET", "user")
        return {"ok": code == 200, "provider": self.provider,
                "login": data.get("login") if isinstance(data, dict) else None,
                "request_id": headers.get("X-GitHub-Request-Id")}

    def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        _, data, _ = self._request("GET", self._repo(owner, repo))
        return data

    def list_issues(self, owner: str, repo: str, *, state: str = "open", per_page: int = 30,
                    page: int = 1, labels: Optional[list[str]] = None) -> list[dict[str, Any]]:
        if state not in {"open", "closed", "all"}: raise ValueError("invalid issue state")
        if not 1 <= per_page <= 100 or page < 1: raise ValueError("invalid pagination")
        params: dict[str, Any] = {"state": state, "per_page": per_page, "page": page}
        if labels: params["labels"] = ",".join(labels)
        _, data, _ = self._request("GET", f"{self._repo(owner, repo)}/issues", params=params)
        return [x for x in (data or []) if "pull_request" not in x]

    def get_issue(self, owner: str, repo: str, issue_number: int) -> dict[str, Any]:
        _, data, _ = self._request("GET", f"{self._repo(owner, repo)}/issues/{int(issue_number)}")
        return data

    def create_issue(self, owner: str, repo: str, *, title: str, body: str = "",
                     labels: Optional[list[str]] = None, assignees: Optional[list[str]] = None) -> dict[str, Any]:
        self._require_mutation()
        if not title.strip(): raise ValueError("issue title is required")
        payload: dict[str, Any] = {"title": title.strip(), "body": body}
        if labels: payload["labels"] = labels
        if assignees: payload["assignees"] = assignees
        try:
            _, data, headers = self._request("POST", f"{self._repo(owner, repo)}/issues", json=payload, mutation=True)
            if not isinstance(data, dict) or not data.get("id"):
                return self._receipt(status="uncertain", request_id=headers.get("X-GitHub-Request-Id"),
                    error="GitHub accepted/returned a response without an authoritative issue ID",
                    metadata={"operation": "create_issue", "reconciliation_required": True})
            return self._receipt(status="succeeded", object_id=data["id"],
                request_id=headers.get("X-GitHub-Request-Id"), metadata={"issue_number": data.get("number"), "html_url": data.get("html_url"), "node_id": data.get("node_id")})
        except ProviderAdapterError as exc: return self._handle_mutation_error(exc)

    def add_issue_comment(self, owner: str, repo: str, issue_number: int, *, body: str) -> dict[str, Any]:
        self._require_mutation()
        if not body.strip(): raise ValueError("comment body is required")
        try:
            _, data, headers = self._request("POST", f"{self._repo(owner, repo)}/issues/{int(issue_number)}/comments", json={"body": body}, mutation=True)
            if not isinstance(data, dict) or not data.get("id"):
                return self._receipt(status="uncertain", request_id=headers.get("X-GitHub-Request-Id"), error="Comment response lacked an authoritative ID", metadata={"reconciliation_required": True})
            return self._receipt(status="succeeded", object_id=data["id"], request_id=headers.get("X-GitHub-Request-Id"), metadata={"issue_number": issue_number, "html_url": data.get("html_url")})
        except ProviderAdapterError as exc: return self._handle_mutation_error(exc)

    def list_pull_requests(self, owner: str, repo: str, *, state: str = "open", per_page: int = 30) -> list[dict[str, Any]]:
        if state not in {"open", "closed", "all"}: raise ValueError("invalid pull request state")
        if not 1 <= per_page <= 100: raise ValueError("per_page must be 1..100")
        _, data, _ = self._request("GET", f"{self._repo(owner, repo)}/pulls", params={"state": state, "per_page": per_page})
        return data or []

    def get_workflow_run(self, owner: str, repo: str, run_id: int) -> dict[str, Any]:
        _, data, _ = self._request("GET", f"{self._repo(owner, repo)}/actions/runs/{int(run_id)}")
        return data

    def list_workflow_runs(self, owner: str, repo: str, *, workflow_id: Optional[str] = None,
                           branch: Optional[str] = None, event: Optional[str] = None,
                           per_page: int = 30) -> list[dict[str, Any]]:
        if not 1 <= per_page <= 100: raise ValueError("per_page must be 1..100")
        path = f"{self._repo(owner, repo)}/actions/runs" if workflow_id is None else f"{self._repo(owner, repo)}/actions/workflows/{workflow_id}/runs"
        params: dict[str, Any] = {"per_page": per_page}
        if branch: params["branch"] = branch
        if event: params["event"] = event
        _, data, _ = self._request("GET", path, params=params)
        return (data or {}).get("workflow_runs", [])

    def dispatch_workflow(self, owner: str, repo: str, *, workflow_id: str,
                          ref: str, inputs: Optional[dict[str, str]] = None,
                          confirm: bool = False) -> dict[str, Any]:
        self._require_mutation(high_risk=True, confirm=confirm)
        if not workflow_id or not ref: raise ValueError("workflow_id and ref are required")
        try:
            code, _, headers = self._request("POST", f"{self._repo(owner, repo)}/actions/workflows/{workflow_id}/dispatches",
                json={"ref": ref, "inputs": inputs or {}}, mutation=True)
            # GitHub returns 204 without a run ID. Acceptance is not a terminal
            # execution receipt; keep it uncertain until a run is observed.
            return self._receipt(status="uncertain", request_id=headers.get("X-GitHub-Request-Id"),
                error="Workflow dispatch accepted but run ID is not returned; reconcile workflow runs",
                metadata={"http_status": code, "workflow_id": workflow_id, "ref": ref, "reconciliation_required": True})
        except ProviderAdapterError as exc: return self._handle_mutation_error(exc)

    def rerun_workflow(self, owner: str, repo: str, run_id: int, *, confirm: bool = False) -> dict[str, Any]:
        self._require_mutation(high_risk=True, confirm=confirm)
        try:
            code, _, headers = self._request("POST", f"{self._repo(owner, repo)}/actions/runs/{int(run_id)}/rerun", mutation=True)
            return self._receipt(status="uncertain", object_id=run_id, request_id=headers.get("X-GitHub-Request-Id"),
                error="Rerun accepted/requested; inspect workflow run state before recording terminal success",
                metadata={"http_status": code, "operation": "rerun_workflow", "reconciliation_required": True})
        except ProviderAdapterError as exc: return self._handle_mutation_error(exc)

    def cancel_workflow_run(self, owner: str, repo: str, run_id: int, *, confirm: bool = False) -> dict[str, Any]:
        self._require_mutation(high_risk=True, confirm=confirm)
        try:
            code, _, headers = self._request("POST", f"{self._repo(owner, repo)}/actions/runs/{int(run_id)}/cancel", mutation=True)
            return self._receipt(status="uncertain", object_id=run_id, request_id=headers.get("X-GitHub-Request-Id"),
                error="Cancellation requested; inspect workflow run state before recording terminal success",
                metadata={"http_status": code, "operation": "cancel_workflow_run", "reconciliation_required": True})
        except ProviderAdapterError as exc: return self._handle_mutation_error(exc)

    def merge_pull_request(self, owner: str, repo: str, pull_number: int, *,
                           merge_method: str = "squash", commit_title: Optional[str] = None,
                           confirm: bool = False) -> dict[str, Any]:
        self._require_mutation(high_risk=True, confirm=confirm)
        if merge_method not in {"merge", "squash", "rebase"}: raise ValueError("invalid merge_method")
        payload: dict[str, Any] = {"merge_method": merge_method}
        if commit_title: payload["commit_title"] = commit_title
        try:
            _, data, headers = self._request("PUT", f"{self._repo(owner, repo)}/pulls/{int(pull_number)}/merge", json=payload, mutation=True)
            if isinstance(data, dict) and data.get("sha"):
                return self._receipt(status="succeeded", object_id=data["sha"], request_id=headers.get("X-GitHub-Request-Id"), metadata={"pull_number": pull_number, "merged": data.get("merged"), "message": data.get("message")})
            return self._receipt(status="uncertain", request_id=headers.get("X-GitHub-Request-Id"), error="Merge response lacked authoritative merge commit SHA", metadata={"pull_number": pull_number, "reconciliation_required": True})
        except ProviderAdapterError as exc: return self._handle_mutation_error(exc)

    def reconcile_workflow(self, owner: str, repo: str, *, workflow_id: str,
                           ref: str, started_after: datetime, per_page: int = 100) -> dict[str, Any]:
        """Find candidate workflow runs after an ambiguous dispatch; never guesses absence."""
        if started_after.tzinfo is None: started_after = started_after.replace(tzinfo=timezone.utc)
        runs = self.list_workflow_runs(owner, repo, workflow_id=workflow_id, branch=ref, per_page=per_page)
        threshold = started_after.astimezone(timezone.utc)
        matches = []
        for run in runs:
            created = run.get("created_at")
            try: created_at = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            except (TypeError, ValueError): continue
            if created_at >= threshold and run.get("head_branch") == ref:
                matches.append(run)
        return {"provider": self.provider, "status": "matched" if matches else "inconclusive",
                "matches": matches, "authoritative_absence": False,
                "message": "No matching run is not proof that dispatch failed; provider visibility may lag." if not matches else None}
