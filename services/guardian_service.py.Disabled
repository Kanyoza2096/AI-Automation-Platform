"""
Kanyoza Systems AI Platform v10 — Guardian Service.

v10 wrapper around the Code Guardian scanning subsystem.  Replaces the
inline ``run_scan()`` closure that lived inside ``lifecycle.py`` with a
proper DI-injectable service class.

Responsibilities
----------------
* Orchestrate a full scan cycle: list repos → clone/pull → scan → auto-fix.
* Publish ``ScanCompletedEvent`` to the v10 EventBus (DI-resolved, not the
  module-level singleton) so the SocketIO bridge can relay it to the dashboard.
* Notify the owner via Facebook Messenger with a human-readable summary.
* Record the scan in analytics.
* Expose ``get_status()`` so ``/api/v1/dashboard/live`` has live guardian state.

The class holds no mutable state beyond the cached last-result so it is safe
to call from any thread (the ScanScheduler runs it in a dedicated daemon thread).
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from config.settings import settings as _settings

if TYPE_CHECKING:
    from core.event_bus import EventBus
    from guardian.github_client import GitHubClient
    from guardian.scanner import CodeGuardianScanner, ScanResult
    from guardian.auto_fix import AutoFixEngine
    from services.facebook import FacebookService

logger = logging.getLogger("kanyoza.guardian")

# Directories to skip when counting total files (mirroring v9 logic)
_SKIP_DIRS = {"venv", ".venv", "env", "__pycache__", "node_modules", ".git", "dist", "build"}


class GuardianService:
    """
    Orchestrates Code Guardian scans in the v10 platform.

    Injected dependencies
    ---------------------
    github_client   — authenticated GitHubClient
    scanner         — CodeGuardianScanner (stateless; does the regex work)
    auto_fix_engine — AutoFixEngine (creates PRs for fixable issues)
    event_bus       — DI-resolved EventBus (same instance used by workflows)
    facebook_svc    — FacebookService (sends owner notification)
    """

    def __init__(
        self,
        github_client: "GitHubClient",
        scanner: "CodeGuardianScanner",
        auto_fix_engine: "AutoFixEngine",
        event_bus: "EventBus",
        facebook_svc: "FacebookService",
    ) -> None:
        self._github = github_client
        self._scanner = scanner
        self._auto_fix = auto_fix_engine
        self._bus = event_bus
        self._fb = facebook_svc
        self._last_report: Optional[dict] = None
        self._last_issues: list = []
        self._last_scan_at: Optional[datetime] = None
        self._scan_count: int = 0
        self._scan_lock = threading.Lock()   # prevents concurrent scan runs
        self._state_lock = threading.Lock()  # guards reads/writes of result state
        self._scanning: bool = False

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return _settings.guardian_configured

    def scan_all(self) -> dict:
        """
        Run a full guardian scan cycle and return the report dict.

        Safe to call from a background thread.  All exceptions are caught so
        the ScanScheduler never loses its next scheduled run.

        A threading lock prevents concurrent scans (scheduler + slash command
        overlap). If a scan is already in progress the call returns immediately
        with a ``{"skipped": "scan_in_progress"}`` sentinel so callers can
        distinguish a skip from an actual zero-issue result.
        """
        if not self._scan_lock.acquire(blocking=False):
            logger.info(
                "Code Guardian: scan already in progress — skipping duplicate trigger."
            )
            return {"skipped": "scan_in_progress", "total_issues": 0}

        # Lock acquired — everything from here runs inside the finally so the
        # lock and the _scanning flag are ALWAYS released, even if mkdtemp fails.
        self._scanning = True
        logger.info("Code Guardian: starting scan cycle...")
        temp_dir: Optional[str] = None
        try:
            temp_dir = tempfile.mkdtemp(prefix="guardian_")
            return self._run_scan(temp_dir)
        except Exception as exc:
            logger.error("Guardian scan cycle failed: %s", exc)
            return {"error": str(exc), "total_issues": 0}
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            self._scanning = False
            self._scan_lock.release()

    def get_status(self) -> dict:
        with self._state_lock:
            return {
                "configured": self.is_configured,
                "auto_fix_enabled": _settings.guardian_auto_fix,
                "scan_count": self._scan_count,
                "scanning": self._scanning,
                "last_scan_at": self._last_scan_at.isoformat() if self._last_scan_at else None,
                "last_report": dict(self._last_report) if self._last_report else None,
            }

    def get_issues(self) -> list:
        """Return the full issue list from the last scan (for the /api/v1/guardian/issues endpoint)."""
        with self._state_lock:
            return list(self._last_issues)

    # ── Internal scan logic ────────────────────────────────────────────────────

    def _run_scan(self, temp_dir: str) -> dict:
        # 1 — Validate GitHub connectivity
        if not self._github.validate_token():
            logger.error("Guardian: GitHub token is invalid — aborting scan")
            return {"error": "invalid_github_token", "total_issues": 0}

        # 2 — Discover repositories
        repos = self._github.get_all_repos(exclude=_settings.guardian_exclude_repos)
        if not repos:
            logger.info("Guardian: no repositories found")
            return {"repos_scanned": 0, "files_scanned": 0, "total_issues": 0}

        # 3 — Clone / pull every repo
        repo_paths: list[dict] = []
        for repo in repos:
            try:
                local_path = self._github.clone_or_pull(repo, temp_dir)
                if local_path:
                    repo_paths.append({"name": repo["name"], "local_path": local_path})
            except Exception as exc:
                logger.error("Guardian: failed to clone %s: %s", repo["name"], exc)

        # 4 — Count total files (excluding noise dirs)
        total_files = sum(
            sum(1 for f in Path(rp["local_path"]).rglob("*")
                if f.is_file() and not any(e in f.parts for e in _SKIP_DIRS))
            for rp in repo_paths
            if rp.get("local_path")
        )

        # 5 — Run the scanner
        issues = self._scanner.scan_repos(repo_paths)
        report = self._scanner.build_report(issues, len(repos), total_files)

        # 6 — Auto-fix fixable issues (creates GitHub PRs) — opt-in via GUARDIAN_AUTO_FIX
        if (self._auto_fix.is_configured
                and _settings.guardian_auto_fix
                and report["total_issues"] > 0):
            self._run_auto_fix(repos, report)

        # 7 — Notify owner via Messenger
        self._notify_owner(report)

        # 8 — Publish domain event to the DI EventBus
        self._publish_event(report)

        # 9 — Record analytics
        try:
            from analytics.tracker import analytics
            analytics.record_guardian_scan(
                repos=report.get("repos_scanned", 0),
                files=report.get("files_scanned", 0),
                issues=report.get("total_issues", 0),
                critical=report.get("critical", 0),
                high=report.get("high", 0),
                medium=report.get("medium", 0),
                low=report.get("low", 0),
            )
        except Exception as exc:
            logger.debug("Guardian: analytics record failed: %s", exc)

        # 10 — Cache result (keep issues separately for /api/v1/guardian/issues)
        with self._state_lock:
            self._last_issues = report.get("issues", [])
            self._last_report = {k: v for k, v in report.items() if k != "issues"}
            self._last_scan_at = datetime.now(timezone.utc)
            self._scan_count += 1

        logger.info(
            "Guardian scan complete — repos=%d  files=%d  issues=%d  critical=%d",
            report.get("repos_scanned", 0),
            report.get("files_scanned", 0),
            report.get("total_issues", 0),
            report.get("critical", 0),
        )
        return report

    def _run_auto_fix(self, repos: list, report: dict) -> None:
        for repo in repos:
            repo_full = f"{_settings.github_username}/{repo['name']}"
            repo_issues = [
                i for i in report.get("issues", [])
                if isinstance(i, dict) and i.get("repo") == repo["name"]
            ]
            if not repo_issues:
                continue
            try:
                fix_results = self._auto_fix.auto_fix_all(repo_full, repo_issues)
                if fix_results.get("fixed", 0) > 0:
                    logger.info(
                        "Guardian auto-fixed %d issue(s) in %s",
                        fix_results["fixed"], repo["name"],
                    )
            except Exception as exc:
                logger.error("Guardian auto-fix error for %s: %s", repo["name"], exc)

    def _notify_owner(self, report: dict) -> None:
        owner_psid = getattr(_settings, "owner_psid", "")
        if not owner_psid:
            return
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            if report["total_issues"] == 0:
                msg = (
                    f"🛡️ Code Guardian\n\n"
                    f"📅 {now}\n"
                    f"📂 {report['repos_scanned']} repos | {report['files_scanned']} files\n\n"
                    f"✅ ALL CLEAR — no issues found."
                )
            else:
                msg = (
                    f"🛡️ Code Guardian\n\n"
                    f"📅 {now}\n"
                    f"📂 {report['repos_scanned']} repos | {report['files_scanned']} files\n\n"
                    f"🔴 {report.get('critical', 0)} critical  "
                    f"🟠 {report.get('high', 0)} high  "
                    f"🟡 {report.get('medium', 0)} medium\n"
                    f"⚠️ {report['total_issues']} total issues"
                )
            self._fb.send_message(owner_psid, msg)
        except Exception as exc:
            logger.debug("Guardian: owner notification failed: %s", exc)

    def _publish_event(self, report: dict) -> None:
        try:
            from core.events import ScanCompletedEvent
            self._bus.publish(ScanCompletedEvent(
                repo=_settings.github_username,
                issues_found=report.get("total_issues", 0),
                critical=report.get("critical", 0),
            ))
        except Exception as exc:
            logger.debug("Guardian: event publish failed: %s", exc)
