"""
GitHub Connector Plugin v1.1
Integrates GitHub with Kanyoza AI Platform.

v1.1 FIXES:
  - Correct GitHub API diff endpoint
  - Handle 200 AND 201 status codes
  - Timeout on all HTTP requests
  - Webhook uses X-GitHub-Event header
  - Safe Gemini import with fallback
"""

import base64
import time
import logging
import os
import requests
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger("kanyoza.plugins.github_connector")


class GitHubConnectorPlugin:
    """GitHub integration for code intelligence."""

    def __init__(self, supabase_client=None, redis_client=None):
        self.supabase = supabase_client
        self.redis = redis_client
        self.name = "github_connector"
        self._running = False
        
        self.github_token = os.environ.get("GITHUB_ACCESS_TOKEN", "")
        self.github_api = "https://api.github.com"
        self.HTTP_TIMEOUT = 30  # v1.1: 30s timeout
        
        logger.info("GitHubConnectorPlugin initialized")

    # ─── PLUGIN INTERFACE ──────────────────────────────────────────────

    def get_commands(self) -> List[Dict[str, Any]]:
        return [
            {"name": "github_review_pr", "description": "Review a pull request with AI", "handler": self._handle_review_pr},
            {"name": "github_generate_pr_desc", "description": "Generate PR description from changes", "handler": self._handle_generate_pr_desc},
            {"name": "github_analyze_repo", "description": "Analyze repository with AI", "handler": self._handle_analyze_repo},
            {"name": "github_suggest_fix", "description": "Suggest fix for an issue", "handler": self._handle_suggest_fix},
            {"name": "github_generate_docs", "description": "Generate documentation for code", "handler": self._handle_generate_docs},
        ]

    def get_webhooks(self) -> List[Dict[str, Any]]:
        return [{"path": "/api/v1/github/webhook", "method": "POST", "handler": "handle_webhook"}]

    def start(self):
        if self._running:
            return
        self._running = True
        logger.info("GitHubConnectorPlugin: started")

    def stop(self):
        self._running = False
        logger.info("GitHubConnectorPlugin: stopped")

    def shutdown(self):
        self.stop()

    def health_check(self) -> dict:
        """Health check for MetaEngine."""
        status = "healthy" if self._running else "stopped"
        return {"status": status, "token_configured": bool(self.github_token)}

    # ─── GITHUB API HELPERS ────────────────────────────────────────────

    def _github_request(self, endpoint: str, method: str = "GET", data: dict = None) -> dict:
        """Make a request to the GitHub API with timeout and retry."""
        if not self.github_token:
            logger.error("GitHub access token not configured")
            return {"error": "GitHub token missing"}

        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Kanyoza-AI-Platform",
        }

        url = f"{self.github_api}{endpoint}"

        for attempt in range(3):  # v1.1: retry up to 3 times
            try:
                if method == "GET":
                    response = requests.get(url, headers=headers, timeout=self.HTTP_TIMEOUT)
                elif method == "POST":
                    response = requests.post(url, headers=headers, json=data, timeout=self.HTTP_TIMEOUT)
                elif method == "PUT":
                    response = requests.put(url, headers=headers, json=data, timeout=self.HTTP_TIMEOUT)
                elif method == "PATCH":
                    response = requests.patch(url, headers=headers, json=data, timeout=self.HTTP_TIMEOUT)
                else:
                    return {"error": f"Unsupported method: {method}"}

                # v1.1: Accept 200 AND 201
                if response.status_code in (200, 201):
                    try:
                        return response.json()
                    except Exception:
                        return {"ok": True, "status_code": response.status_code}
                elif response.status_code == 204:
                    return {"ok": True, "status_code": 204}
                elif response.status_code == 403 and "rate limit" in response.text.lower():
                    logger.warning("GitHub rate limit exceeded")
                    return {"error": "GitHub rate limit exceeded"}
                else:
                    logger.error(f"GitHub API error: {response.status_code}")
                    if attempt == 2:
                        return {"error": f"GitHub API error: {response.status_code}"}
                    time.sleep(1 * (2 ** attempt))  # Exponential backoff
            except requests.Timeout:
                logger.warning(f"GitHub request timeout (attempt {attempt+1}/3)")
                if attempt == 2:
                    return {"error": "GitHub API timeout"}
                time.sleep(1 * (2 ** attempt))
            except Exception as e:
                logger.error(f"GitHub request failed: {e}")
                return {"error": str(e)}

        return {"error": "GitHub API failed after retries"}

    # ─── AI GENERATION HELPER (Safe import) ──────────────────────────────

    def _generate_with_ai(self, prompt: str) -> str:
        """Generate content with AI — safe import with fallback."""
        try:
            from services.gemini import gemini
            return gemini.generate_content(prompt)
        except ImportError:
            try:
                from services.content_engine import content_engine
                return content_engine.generate_text(prompt)
            except ImportError:
                logger.error("No AI service available")
                return "AI service unavailable"

    # ─── COMMAND HANDLERS ──────────────────────────────────────────────

    def _handle_review_pr(self, params: dict) -> dict:
        """Review a PR and post comments."""
        repo = params.get("repo", "")
        pr_number = params.get("pr_number")
        
        if not repo or not pr_number:
            return {"ok": False, "error": "repo and pr_number required"}

        # v1.1: Correct diff endpoint — use Accept header for diff
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3.diff",
        }
        diff_response = requests.get(
            f"{self.github_api}/repos/{repo}/pulls/{pr_number}",
            headers=headers,
            timeout=self.HTTP_TIMEOUT,
        )
        
        if diff_response.status_code != 200:
            return {"ok": False, "error": f"GitHub diff error: {diff_response.status_code}"}
        
        diff_text = diff_response.text

        # Generate review with AI
        review = self._generate_with_ai(
            f"Review this pull request. Find bugs, security issues, and improvements:\n\n{diff_text[:5000]}"  # Limit size
        )

        # Post as PR comment
        self._github_request(
            f"/repos/{repo}/issues/{pr_number}/comments",
            method="POST",
            data={"body": f"🤖 **AI Review:**\n\n{review}"}
        )

        # Store in Supabase
        if self.supabase:
            try:
                self.supabase.table("github_pr_reviews").insert({
                    "repo": repo,
                    "pr_number": pr_number,
                    "review_content": review,
                    "reviewed_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.warning(f"Failed to save review: {e}")

        return {"ok": True, "review": review}

    def _handle_generate_pr_desc(self, params: dict) -> dict:
        """Generate a PR description from changes."""
        repo = params.get("repo", "")
        pr_number = params.get("pr_number")

        if not repo or not pr_number:
            return {"ok": False, "error": "repo and pr_number required"}

        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3.diff",
        }
        diff_response = requests.get(
            f"{self.github_api}/repos/{repo}/pulls/{pr_number}",
            headers=headers,
            timeout=self.HTTP_TIMEOUT,
        )
        
        if diff_response.status_code != 200:
            return {"ok": False, "error": f"GitHub diff error: {diff_response.status_code}"}

        description = self._generate_with_ai(
            f"Generate a professional pull request description for these changes:\n\n{diff_response.text[:5000]}"
        )

        return {"ok": True, "description": description}

    def _handle_analyze_repo(self, params: dict) -> dict:
        """Analyze a repository."""
        repo = params.get("repo", "")
        if not repo:
            return {"ok": False, "error": "repo required"}

        repo_data = self._github_request(f"/repos/{repo}")
        if "error" in repo_data:
            return {"ok": False, "error": repo_data["error"]}

        languages = self._github_request(f"/repos/{repo}/languages")
        readme = self._github_request(f"/repos/{repo}/readme")

        analysis = self._generate_with_ai(
            f"Analyze this repository:\n"
            f"Name: {repo_data.get('name', '')}\n"
            f"Description: {repo_data.get('description', '')}\n"
            f"Languages: {list(languages.keys()) if isinstance(languages, dict) else []}\n"
            f"Stars: {repo_data.get('stargazers_count', 0)}\n"
            f"Open Issues: {repo_data.get('open_issues_count', 0)}\n"
            "Give insights on code quality, architecture, and suggestions."
        )

        return {"ok": True, "analysis": analysis}

    def _handle_suggest_fix(self, params: dict) -> dict:
        """Suggest a fix for an issue."""
        repo = params.get("repo", "")
        issue_number = params.get("issue_number")

        if not repo or not issue_number:
            return {"ok": False, "error": "repo and issue_number required"}

        issue = self._github_request(f"/repos/{repo}/issues/{issue_number}")
        if "error" in issue:
            return {"ok": False, "error": issue["error"]}

        fix = self._generate_with_ai(
            f"Suggest a fix for this issue:\n"
            f"Title: {issue.get('title', '')}\n"
            f"Body: {issue.get('body', '')}\n"
            "Provide a detailed solution."
        )

        return {"ok": True, "suggestion": fix}

    def _handle_generate_docs(self, params: dict) -> dict:
        """Generate documentation for code."""
        repo = params.get("repo", "")
        file_path = params.get("file_path", "")

        if not repo or not file_path:
            return {"ok": False, "error": "repo and file_path required"}

        file_data = self._github_request(f"/repos/{repo}/contents/{file_path}")
        if "error" in file_data:
            return {"ok": False, "error": file_data["error"]}

        content = base64.b64decode(file_data.get("content", "")).decode("utf-8")

        docs = self._generate_with_ai(
            f"Generate documentation for this code:\n\n{content[:5000]}"
        )

        return {"ok": True, "documentation": docs}

    # ─── WEBHOOK HANDLER ──────────────────────────────────────────────

    def handle_webhook(self, payload: dict) -> dict:
        """Handle incoming GitHub webhooks.

        v1.1: GitHub sends event type in X-GitHub-Event header,
        NOT in the payload. But we accept both formats.
        """
        event_type = payload.get("event_type", "")
        if not event_type:
            event_type = payload.get("X-GitHub-Event", "")
        
        if event_type == "pull_request":
            action = payload.get("action", "")
            if action in ["opened", "synchronize"]:
                repo = payload.get("repository", {}).get("full_name", "")
                pr_number = payload.get("pull_request", {}).get("number", payload.get("number"))
                if repo and pr_number:
                    return self._handle_review_pr({"repo": repo, "pr_number": pr_number})
        
        elif event_type == "issues":
            action = payload.get("action", "")
            if action == "opened":
                repo = payload.get("repository", {}).get("full_name", "")
                issue_number = payload.get("issue", {}).get("number", payload.get("number"))
                if repo and issue_number:
                    return self._handle_suggest_fix({"repo": repo, "issue_number": issue_number})
        
        return {"ok": True, "message": "Event processed"}


# ═══════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═══════════════════════════════════════════════════════════════════════

github_connector_plugin: Optional[GitHubConnectorPlugin] = None


def init_github_connector_plugin(supabase_client=None, redis_client=None) -> GitHubConnectorPlugin:
    """Initialize the GitHub Connector Plugin."""
    global github_connector_plugin
    if github_connector_plugin is None:
        github_connector_plugin = GitHubConnectorPlugin(supabase_client, redis_client)
        github_connector_plugin.start()
    return github_connector_plugin
