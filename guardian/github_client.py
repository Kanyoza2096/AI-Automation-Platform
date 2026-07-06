"""
GitHub API client and local repository manager for Code Guardian.
Fetches the repository list via the GitHub REST API and clones/pulls
repos locally using git shell commands for subsequent scanning.
"""

import logging
import os
import subprocess
import tempfile
from typing import Dict, List, Optional

from utils.http_client import HttpClient

logger = logging.getLogger("kanyoza.guardian.github")


def _run_git(cmd: list, timeout: int) -> subprocess.CompletedProcess:
    """Run a git subprocess with byte-mode pipes to avoid TextIOWrapper issues.

    Uses raw byte pipes (no ``text=True``, no ``capture_output``) and decodes
    manually.  This is safe under all WSGI concurrency models (gthread, sync,
    gevent) because it never touches Python's TextIOWrapper layer.
    """
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,   # bytes — avoids TextIOWrapper
        stderr=subprocess.PIPE,   # bytes — avoids TextIOWrapper
        timeout=timeout,
    )
    return subprocess.CompletedProcess(
        args=proc.args,
        returncode=proc.returncode,
        stdout=(proc.stdout or b"").decode("utf-8", errors="replace"),
        stderr=(proc.stderr or b"").decode("utf-8", errors="replace"),
    )

GITHUB_API = "https://api.github.com"
_REQUEST_TIMEOUT = 20


class GitHubClient:
    def __init__(self, username: str, token: str):
        self._username = username
        self._token = token
        self._session = HttpClient(
            "github",
            base_url=GITHUB_API,
            default_timeout=_REQUEST_TIMEOUT,
            default_headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "KanyozaSystemsBot/1.0",
            },
        )

    # ------------------------------------------------------------------
    # Repository listing
    # ------------------------------------------------------------------

    def get_all_repos(self, exclude: Optional[List[str]] = None) -> List[Dict]:
        """
        Fetch all non-archived repositories for the authenticated user.
        Paginates automatically. Filters out repos in the exclude list.
        """
        exclude_set = set(exclude or [])
        repos = []
        page = 1

        while True:
            try:
                resp = self._session.get(
                    f"{GITHUB_API}/user/repos",
                    params={
                        "visibility": "all",
                        "affiliation": "owner",
                        "per_page": 100,
                        "page": page,
                        "sort": "updated",
                    },
                    timeout=_REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break

                for repo in batch:
                    if repo.get("archived"):
                        continue
                    if repo.get("name") in exclude_set:
                        continue
                    repos.append({
                        "name": repo["name"],
                        "full_name": repo["full_name"],
                        "clone_url": repo["clone_url"],
                        "ssh_url": repo["ssh_url"],
                        "default_branch": repo.get("default_branch", "main"),
                        "language": repo.get("language"),
                        "private": repo.get("private", False),
                        "updated_at": repo.get("updated_at"),
                    })

                if len(batch) < 100:
                    break
                page += 1

            except Exception as exc:
                logger.error("GitHub API error (page %d): %s", page, exc)
                break

        logger.info("Found %d repositories for %s", len(repos), self._username)
        return repos

    def get_repo(self, repo_name: str) -> Optional[Dict]:
        """Fetch metadata for a single repository."""
        try:
            resp = self._session.get(
                f"{GITHUB_API}/repos/{self._username}/{repo_name}",
                timeout=_REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.error("get_repo failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Local cloning / pulling
    # ------------------------------------------------------------------

    def clone_or_pull(self, repo: Dict, parent_dir: str) -> Optional[str]:
        """
        Clone the repository into parent_dir if it doesn't exist,
        or pull the latest changes if it does.
        Returns the local path on success, None on failure.
        """
        repo_name = repo["name"]
        local_path = os.path.join(parent_dir, repo_name)
        clone_url = self._authenticated_url(repo["clone_url"])

        try:
            if os.path.isdir(os.path.join(local_path, ".git")):
                return self._pull(local_path, repo.get("default_branch", "main"))
            else:
                return self._clone(clone_url, local_path)
        except Exception as exc:
            logger.error("clone_or_pull failed for %s: %s", repo_name, exc)
            return None

    def _clone(self, clone_url: str, local_path: str) -> Optional[str]:
        logger.debug("Cloning into %s", local_path)
        result = _run_git(
            ["git", "clone", "--depth=1", clone_url, local_path],
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("Cloned: %s", local_path)
            return local_path
        # Mask any embedded token before logging (token may appear in git error output)
        safe_stderr = result.stderr[:300]
        if self._token:
            safe_stderr = safe_stderr.replace(self._token, "***")
        logger.error("git clone failed: %s", safe_stderr)
        return None

    def _pull(self, local_path: str, branch: str) -> Optional[str]:
        logger.debug("Pulling %s", local_path)
        result = _run_git(
            ["git", "-C", local_path, "pull", "--ff-only", "origin", branch],
            timeout=60,
        )
        if result.returncode == 0:
            return local_path
        # Non-fatal — return existing checkout
        logger.warning("git pull failed for %s (using existing): %s", local_path, result.stderr[:200])
        return local_path

    def _authenticated_url(self, clone_url: str) -> str:
        """Inject token into HTTPS clone URL for authentication."""
        if clone_url.startswith("https://") and self._token:
            return clone_url.replace(
                "https://",
                f"https://{self._username}:{self._token}@",
                1,
            )
        return clone_url

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_token(self) -> bool:
        try:
            resp = self._session.get(
                f"{GITHUB_API}/user",
                timeout=_REQUEST_TIMEOUT,
            )
            return resp.status_code == 200
        except Exception:
            return False
