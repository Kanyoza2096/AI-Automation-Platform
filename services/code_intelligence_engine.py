"""
KANYOZA CODE INTELLIGENCE ENGINE v3.0 — HYBRID ASYNC
=====================================================

Single-file repository intelligence engine.

v3.0:
- Async internals with sync-compatible public API
- EventBus async publish handled correctly
- GitHub/Gemini/file operations offloaded to executor
- HealthProvider protocol support
- v2-compatible public method names/signatures
- No threading.Thread
- No threading.Lock/RLock
- asyncio synchronization primitives are created lazily in async context

Public API:
    start(interval_hours=6)
    stop()
    scan_now()
    get_status()
    get_issues(severity=None, limit=100)
    get_report()
    get_scan_history(limit=20)
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import inspect
import json
import logging
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from config.settings import settings as _settings
except Exception:
    _settings = None

logger = logging.getLogger("kanyoza.code_intel")

_SKIP_DIRS = {
    "venv", ".venv", "env", "__pycache__", "node_modules", ".git",
    "dist", "build", ".next", "out", "coverage", ".pytest_cache",
    ".mypy_cache",
}

_SCANNABLE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".sql", ".yaml", ".yml",
    ".json", ".toml",
}

_MAX_FILE_BYTES = 200_000
_MAX_MODEL_CHARS = 8_000
_MAX_CHUNK_CHARS = 7_000
_MAX_CHUNKS_PER_FILE = 12
_MAX_FILES_PER_SCAN = 500

_ALLOWED_SEVERITIES = {"critical", "high", "medium", "low"}
_ALLOWED_TYPES = {
    "security", "bug", "performance", "error_handling",
    "deprecated", "configuration",
}

_GEMINI_METHODS = (
    "generate", "generate_content", "generate_text",
    "complete", "chat", "ask",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"true", "yes", "1", "y"}:
            return True
        if value in {"false", "no", "0", "n"}:
            return False
    return default


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def get_setting(name: str, default: Any = None) -> Any:
    if _settings is None:
        return default
    return getattr(_settings, name, default)


def extract_json_payload(raw: Any) -> Any:
    """Extract JSON from common Gemini response shapes."""
    if raw is None:
        return None

    if isinstance(raw, (list, dict)):
        return raw

    candidates: List[str] = []

    if isinstance(raw, str):
        candidates.append(raw)
    else:
        for attr in ("text", "output_text", "content", "response"):
            try:
                value = getattr(raw, attr, None)
            except Exception:
                value = None
            if isinstance(value, str) and value.strip():
                candidates.append(value)

        if isinstance(raw, dict):
            for key in ("text", "output_text", "content", "response"):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value)

    for candidate in candidates:
        text = candidate.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        fenced = re.search(
            r"```(?:json)?\s*(.*?)\s*```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fenced:
            try:
                return json.loads(fenced.group(1).strip())
            except json.JSONDecodeError:
                pass

        start, end = text.find("["), text.rfind("]")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

    return None


def iter_candidate_files(root: Path) -> List[Path]:
    """Iterate candidate files with hard scan bounds."""
    files: List[Path] = []

    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
            if path.suffix.lower() not in _SCANNABLE_EXTENSIONS:
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.stat().st_size >= _MAX_FILE_BYTES:
                continue

            files.append(path)

            if len(files) >= _MAX_FILES_PER_SCAN:
                break
        except OSError:
            continue

    return files


def chunk_text(
    text: str,
    max_chars: int = _MAX_CHUNK_CHARS,
) -> List[Tuple[int, str]]:
    """Split source by line boundaries."""
    lines = text.splitlines()
    chunks: List[Tuple[int, str]] = []
    current: List[str] = []
    current_chars = 0
    start_line = 1

    for line_number, line in enumerate(lines, start=1):
        size = len(line) + 1

        if current and current_chars + size > max_chars:
            chunks.append((start_line, "\n".join(current)))
            current = []
            current_chars = 0
            start_line = line_number

        current.append(line)
        current_chars += size

    if current:
        chunks.append((start_line, "\n".join(current)))

    return chunks


class CodeIntelligenceEngine:
    VERSION = "3.0"

    def __init__(
        self,
        github_client: Any = None,
        event_bus: Any = None,
        container: Any = None,
    ) -> None:
        self._github = github_client
        self._bus = event_bus
        self._container = container

        # Created lazily inside an async context so the engine can be
        # instantiated safely from synchronous application bootstrap code.
        self._state_lock: Optional[asyncio.Lock] = None
        self._scan_lock: Optional[asyncio.Lock] = None

        self._running = False
        self._scanning = False
        self._scan_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        self._scan_interval_hours = 6
        self._last_issues: List[dict] = []
        self._last_report: Optional[dict] = None
        self._last_scan_at: Optional[datetime] = None
        self._scan_count = 0
        self._total_issues_found = 0
        self._auto_fixes_created = 0
        self._start_time = utcnow()
        self._last_scan_duration_ms = 0.0

        self._scan_history: List[dict] = []
        self._max_scan_history = 100
        self._issue_index: Dict[str, dict] = {}
        self._max_issue_index = 10_000

        logger.info("CodeIntelligenceEngine v%s initialized", self.VERSION)

    # ------------------------------------------------------------------
    # ASYNC STATE PRIMITIVES
    # ------------------------------------------------------------------

    def _ensure_async_primitives(self) -> None:
        if self._state_lock is None:
            self._state_lock = asyncio.Lock()
        if self._scan_lock is None:
            self._scan_lock = asyncio.Lock()
        if self._stop_event is None:
            self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # HEALTH PROVIDER PROTOCOL
    # ------------------------------------------------------------------

    @property
    def service_name(self) -> str:
        return "code_intelligence"

    def check_health(self) -> dict:
        status = self.get_status()
        return {
            "status": "healthy" if status["running"] else "stopped",
            "version": self.VERSION,
            "scanning": status["scanning"],
            "scan_count": status["scan_count"],
            "total_issues_found": status["total_issues_found"],
        }

    async def check_health_async(self) -> dict:
        return self.check_health()

    # ------------------------------------------------------------------
    # STATUS — FAST SYNCHRONOUS READS
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        return {
            "engine": "code_intelligence",
            "version": self.VERSION,
            "running": self._running,
            "scanning": self._scanning,
            "scan_count": self._scan_count,
            "total_issues_found": self._total_issues_found,
            "auto_fixes_created": self._auto_fixes_created,
            "last_scan_at": iso(self._last_scan_at),
            "last_report": (
                json.loads(json.dumps(self._last_report, default=str))
                if self._last_report
                else None
            ),
            "uptime_hours": round(
                (utcnow() - self._start_time).total_seconds() / 3600,
                2,
            ),
            "history_count": len(self._scan_history),
            "last_scan_duration_ms": round(self._last_scan_duration_ms, 1),
        }

    def get_issues(
        self,
        severity: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        limit = max(1, min(safe_int(limit, 100), 1000))
        issues = list(self._last_issues)

        if severity:
            severity = severity.strip().lower()
            issues = [i for i in issues if i.get("severity") == severity]

        return json.loads(json.dumps(issues[:limit], default=str))

    def get_report(self) -> Optional[dict]:
        if not self._last_report:
            return None
        return json.loads(json.dumps(self._last_report, default=str))

    def get_scan_history(self, limit: int = 20) -> List[dict]:
        limit = max(1, min(safe_int(limit, 20), self._max_scan_history))
        return json.loads(
            json.dumps(list(reversed(self._scan_history[-limit:])), default=str)
        )

    # ------------------------------------------------------------------
    # LIFECYCLE — SYNC-COMPATIBLE
    # ------------------------------------------------------------------

    def start(self, interval_hours: int = 6) -> None:
        """Start the engine and schedule its async lifecycle task."""
        if self._running:
            return

        self._running = True
        self._scan_interval_hours = max(1, safe_int(interval_hours, 6))
        self._start_time = utcnow()

        self._schedule_async(self._async_start())

        logger.info(
            "CodeIntelligenceEngine started (every %dh)",
            self._scan_interval_hours,
        )
        self._emit_sync(
            "code_intelligence_started",
            {
                "version": self.VERSION,
                "interval_hours": self._scan_interval_hours,
            },
        )

    def stop(self) -> None:
        """Stop the engine without requiring the caller to await anything."""
        self._running = False

        task = self._scan_task
        if task and not task.done():
            task.cancel()

        self._scan_task = None

        if self._stop_event is not None:
            try:
                self._stop_event.set()
            except RuntimeError:
                pass

        logger.info("CodeIntelligenceEngine stopped")
        self._emit_sync(
            "code_intelligence_stopped",
            {"version": self.VERSION},
        )

    def scan_now(self) -> dict:
        """
        Sync-compatible scan entry point.

        Outside an active event loop, this blocks until the scan completes.
        Inside an active event loop, it schedules the scan and immediately
        returns a scheduled status because a synchronous method cannot block
        the currently running event loop.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._async_scan_now())

        asyncio.create_task(self._async_scan_now(), name="code-intel-manual-scan")
        return {
            "status": "scheduled",
            "reason": "scan_running_async",
        }

    # ------------------------------------------------------------------
    # ASYNC LIFECYCLE
    # ------------------------------------------------------------------

    async def _async_start(self) -> None:
        self._ensure_async_primitives()
        self._event_loop = asyncio.get_running_loop()

        if self._stop_event is None:
            self._stop_event = asyncio.Event()

        self._scan_task = asyncio.create_task(
            self._async_scan_loop(),
            name="code-intel-engine",
        )

    async def _async_scan_loop(self) -> None:
        self._ensure_async_primitives()

        try:
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=60,
                )
            except asyncio.TimeoutError:
                pass

            if self._running:
                try:
                    await self._async_scan_now()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "CodeIntel: initial scheduled scan failed"
                    )

            while self._running:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._scan_interval_hours * 3600,
                    )
                except asyncio.TimeoutError:
                    pass

                if not self._running:
                    break

                try:
                    await self._async_scan_now()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "CodeIntel: scheduled scan failed"
                    )
        except asyncio.CancelledError:
            logger.debug("CodeIntel scan loop cancelled")
            raise

    async def _async_scan_now(self) -> dict:
        self._ensure_async_primitives()

        assert self._scan_lock is not None

        if self._scan_lock.locked():
            return {
                "status": "skipped",
                "reason": "scan_in_progress",
                "total_issues": 0,
            }

        async with self._scan_lock:
            if self._scanning:
                return {
                    "status": "skipped",
                    "reason": "scan_in_progress",
                    "total_issues": 0,
                }

            self._scanning = True
            started = utcnow()
            temp_dir: Optional[str] = None

            self._emit_sync(
                "code_intelligence_scan_started",
                {
                    "started_at": iso(started),
                    "scan_number": self._scan_count + 1,
                },
            )

            try:
                loop = asyncio.get_running_loop()
                temp_dir = await loop.run_in_executor(
                    None,
                    lambda: tempfile.mkdtemp(prefix="code_intel_"),
                )

                return await self._async_run_full_scan(
                    temp_dir,
                    started,
                )

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                completed = utcnow()
                logger.exception("Code Intelligence scan failed")

                report = {
                    "scan_type": "code_intelligence",
                    "engine_version": self.VERSION,
                    "status": "failed",
                    "started_at": iso(started),
                    "completed_at": iso(completed),
                    "duration_seconds": round(
                        (completed - started).total_seconds(),
                        3,
                    ),
                    "repos_scanned": 0,
                    "repos_with_issues": 0,
                    "files_scanned": 0,
                    "total_issues": 0,
                    "critical": 0,
                    "high": 0,
                    "medium": 0,
                    "low": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }

                self._commit_report(report)
                self._emit_sync(
                    "code_intelligence_scan_failed",
                    report,
                )
                return report

            finally:
                if temp_dir:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: shutil.rmtree(
                            temp_dir,
                            ignore_errors=True,
                        ),
                    )

                self._scanning = False

    async def _async_run_full_scan(
        self,
        temp_dir: str,
        scan_started: datetime,
    ) -> dict:
        if not self._github:
            raise RuntimeError("GitHub client not configured")

        loop = asyncio.get_running_loop()

        await loop.run_in_executor(
            None,
            self._validate_github_client,
        )

        repos = await loop.run_in_executor(
            None,
            self._get_repositories,
        )

        if not repos:
            completed = utcnow()

            report = {
                "scan_type": "code_intelligence",
                "engine_version": self.VERSION,
                "status": "completed",
                "started_at": iso(scan_started),
                "completed_at": iso(completed),
                "duration_seconds": round(
                    (completed - scan_started).total_seconds(),
                    3,
                ),
                "repos_scanned": 0,
                "repos_with_issues": 0,
                "files_scanned": 0,
                "total_issues": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "issues": [],
                "repositories": [],
                "message": "No repositories available for scanning",
            }

            self._commit_report(report)
            self._emit_sync(
                "code_intelligence_scan_completed",
                report,
            )
            return report

        all_issues: List[dict] = []
        files_scanned = 0
        repos_with_issues = 0
        repo_results: List[dict] = []

        for repo in repos:
            if self._stop_event and self._stop_event.is_set():
                break

            repo_name = self._repo_name(repo)

            try:
                local_path = await loop.run_in_executor(
                    None,
                    self._clone_repository,
                    repo,
                    temp_dir,
                )

                if not local_path:
                    repo_results.append(
                        {
                            "repository": repo_name,
                            "status": "failed",
                            "error": "repository_clone_returned_empty_path",
                            "issues": 0,
                        }
                    )
                    continue

                repo_issues, file_count = await self._async_scan_repository(
                    repo_name,
                    Path(local_path),
                    repo,
                )

                files_scanned += file_count

                if repo_issues:
                    repos_with_issues += 1
                    all_issues.extend(repo_issues)

                repo_results.append(
                    {
                        "repository": repo_name,
                        "status": "completed",
                        "files_scanned": file_count,
                        "issues": len(repo_issues),
                        "commit_sha": self._repo_commit_sha(repo),
                    }
                )

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                logger.exception(
                    "CodeIntel: failed to scan repository %s",
                    repo_name,
                )
                repo_results.append(
                    {
                        "repository": repo_name,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                        "issues": 0,
                    }
                )

        all_issues = self._deduplicate_findings(all_issues)

        counts = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
        }

        for issue in all_issues:
            severity = issue.get("severity", "low")
            if severity in counts:
                counts[severity] += 1

        completed = utcnow()

        status = (
            "cancelled"
            if self._stop_event and self._stop_event.is_set()
            else "completed"
        )

        self._last_scan_duration_ms = (
            completed - scan_started
        ).total_seconds() * 1000

        report = {
            "scan_type": "code_intelligence",
            "engine_version": self.VERSION,
            "status": status,
            "started_at": iso(scan_started),
            "completed_at": iso(completed),
            "duration_seconds": round(
                (completed - scan_started).total_seconds(),
                3,
            ),
            "repos_scanned": len(repos),
            "repos_with_issues": repos_with_issues,
            "files_scanned": files_scanned,
            "total_issues": len(all_issues),
            **counts,
            "issues": all_issues,
            "repositories": repo_results,
        }

        self._commit_report(report)

        if status == "completed":
            await self._async_run_optional_integrations(
                report,
                repos,
                all_issues,
            )

        self._emit_sync(
            "code_intelligence_scan_completed",
            {
                "status": report["status"],
                "total_issues": report["total_issues"],
                "critical": report["critical"],
                "high": report["high"],
                "medium": report["medium"],
                "low": report["low"],
                "repos_scanned": report["repos_scanned"],
                "files_scanned": report["files_scanned"],
            },
        )

        logger.info(
            "CodeIntel: %d repos, %d files, %d issues "
            "(%d critical, %d high)",
            len(repos),
            files_scanned,
            len(all_issues),
            counts["critical"],
            counts["high"],
        )

        return report

    async def _async_scan_repository(
        self,
        repo_name: str,
        local_path: Path,
        repo: Any,
    ) -> Tuple[List[dict], int]:
        issues: List[dict] = []
        loop = asyncio.get_running_loop()

        files = await loop.run_in_executor(
            None,
            iter_candidate_files,
            local_path,
        )

        for file_path in files:
            if self._stop_event and self._stop_event.is_set():
                break

            try:
                relative_path = str(
                    file_path.relative_to(local_path)
                )

                code = await loop.run_in_executor(
                    None,
                    lambda p=file_path: p.read_text(
                        encoding="utf-8",
                        errors="ignore",
                    ),
                )

                if len(code.strip()) < 10:
                    continue

                file_issues = await self._async_analyze_file(
                    repo_name,
                    relative_path,
                    code,
                    repo,
                )
                issues.extend(file_issues)

            except Exception as exc:
                logger.warning(
                    "CodeIntel: failed to analyze %s: %s",
                    file_path,
                    exc,
                )

        return self._deduplicate_findings(issues), len(files)

    async def _async_analyze_file(
        self,
        repo_name: str,
        file_path: str,
        code: str,
        repo: Any,
    ) -> List[dict]:
        chunks = chunk_text(code)[:_MAX_CHUNKS_PER_FILE]
        results: List[dict] = []

        for chunk_index, (start_line, chunk) in enumerate(chunks):
            try:
                chunk_issues = await self._async_analyze_with_gemini(
                    repo=repo_name,
                    file_path=file_path,
                    code=chunk,
                    start_line=start_line,
                    chunk_index=chunk_index,
                    total_chunks=len(chunks),
                    commit_sha=self._repo_commit_sha(repo),
                )
                results.extend(chunk_issues)

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                logger.warning(
                    "CodeIntel: Gemini analysis failed for %s/%s: %s",
                    repo_name,
                    file_path,
                    exc,
                )

        return self._normalize_findings(
            results,
            repo_name,
            file_path,
            self._repo_commit_sha(repo),
        )

    async def _async_analyze_with_gemini(
        self,
        repo: str,
        file_path: str,
        code: str,
        start_line: int,
        chunk_index: int,
        total_chunks: int,
        commit_sha: Optional[str],
    ) -> List[dict]:
        try:
            from services.gemini import gemini
        except Exception as exc:
            raise RuntimeError(
                f"Gemini service unavailable: {exc}"
            ) from exc

        prompt = self._build_analysis_prompt(
            repo,
            file_path,
            code,
            start_line,
            chunk_index,
            total_chunks,
            commit_sha,
        )

        loop = asyncio.get_running_loop()

        raw_response = await loop.run_in_executor(
            None,
            self._call_gemini,
            gemini,
            prompt,
        )

        payload = extract_json_payload(raw_response)

        if payload is None:
            logger.warning(
                "CodeIntel: Gemini returned non-JSON output for %s/%s",
                repo,
                file_path,
            )
            return []

        if isinstance(payload, dict):
            payload = payload.get("issues", [])

        if not isinstance(payload, list):
            return []

        return [
            item
            for item in payload
            if isinstance(item, dict)
        ]

    # ------------------------------------------------------------------
    # SYNC HELPERS — RUN IN EXECUTORS WHERE THEY BLOCK
    # ------------------------------------------------------------------

    def _validate_github_client(self) -> None:
        validator = getattr(
            self._github,
            "validate_token",
            None,
        )

        if not callable(validator):
            return

        result = validator()

        if inspect.isawaitable(result):
            raise RuntimeError(
                "GitHub validate_token() must be synchronous "
                "when called by the executor adapter"
            )

        if result is False:
            raise RuntimeError("invalid_github_token")

    def _get_repositories(self) -> List[Any]:
        getter = getattr(
            self._github,
            "get_all_repos",
            None,
        )

        if not callable(getter):
            raise RuntimeError(
                "GitHub client does not expose get_all_repos()"
            )

        excluded = get_setting(
            "guardian_exclude_repos",
            [],
        ) or []

        try:
            repos = getter(exclude=excluded)
        except TypeError:
            repos = getter()

        if inspect.isawaitable(repos):
            raise RuntimeError(
                "GitHub get_all_repos() must be synchronous "
                "when called by the executor adapter"
            )

        if not repos:
            return []

        if isinstance(repos, dict):
            repos = repos.get("repositories", [])

        return list(repos)

    @staticmethod
    def _repo_name(repo: Any) -> str:
        if isinstance(repo, dict):
            return str(
                repo.get("name")
                or repo.get("full_name")
                or repo.get("repository")
                or "unknown"
            )

        return str(
            getattr(repo, "name", None)
            or getattr(repo, "full_name", None)
            or repo
        )

    @staticmethod
    def _repo_commit_sha(
        repo: Any,
    ) -> Optional[str]:
        if isinstance(repo, dict):
            for key in (
                "commit_sha",
                "sha",
                "default_branch_sha",
                "head_sha",
            ):
                if repo.get(key):
                    return str(repo[key])

        for attr in (
            "commit_sha",
            "sha",
            "default_branch_sha",
            "head_sha",
        ):
            value = getattr(repo, attr, None)
            if value:
                return str(value)

        return None

    def _clone_repository(
        self,
        repo: Any,
        temp_dir: str,
    ) -> Optional[str]:
        clone = getattr(
            self._github,
            "clone_or_pull",
            None,
        )

        if not callable(clone):
            raise RuntimeError(
                "GitHub client does not expose clone_or_pull()"
            )

        result = clone(repo, temp_dir)

        if inspect.isawaitable(result):
            raise RuntimeError(
                "GitHub clone_or_pull() must be synchronous "
                "when called by the executor adapter"
            )

        return result

    @staticmethod
    def _build_analysis_prompt(
        repo: str,
        file_path: str,
        code: str,
        start_line: int,
        chunk_index: int,
        total_chunks: int,
        commit_sha: Optional[str],
    ) -> str:
        extension = (
            Path(file_path).suffix.lower().lstrip(".")
        )

        return f"""
You are the Code Intelligence component of a production software platform.

Perform a strict senior-engineer security and correctness review.

Repository: {repo}
Commit SHA: {commit_sha or "unknown"}
File: {file_path}
Chunk: {chunk_index + 1}/{total_chunks}
Approximate starting line: {start_line}

Only report REAL, ACTIONABLE problems supported by the supplied code.

Return ONLY a JSON array:
[
  {{
    "severity": "critical|high|medium|low",
    "type": "security|bug|performance|error_handling|deprecated|configuration",
    "line": 123,
    "title": "Short actionable title",
    "description": "Concrete defect and impact.",
    "suggested_fix": "Specific practical fix.",
    "auto_fixable": true
  }}
]

If there are no real issues, return [].

SOURCE:
```{extension}
{code[:_MAX_MODEL_CHARS]}
```
""".strip()

    def _call_gemini(
        self,
        gemini: Any,
        prompt: str,
    ) -> Any:
        """
        Adapt several common Gemini service APIs to one synchronous call.

        The call deliberately runs inside an executor. If the installed
        service exposes only an async API, this adapter executes that
        coroutine in a private event loop owned by the executor thread.
        """
        method = None

        for name in _GEMINI_METHODS:
            candidate = getattr(gemini, name, None)
            if callable(candidate):
                method = candidate
                break

        if method is None:
            raise RuntimeError(
                "Gemini service exposes no supported generation method"
            )

        attempts = (
            lambda: method(prompt),
            lambda: method(content=prompt),
            lambda: method(text=prompt),
            lambda: method(prompt=prompt),
        )

        last_type_error: Optional[Exception] = None

        for attempt in attempts:
            try:
                result = attempt()
                if inspect.isawaitable(result):
                    return asyncio.run(result)
                return result
            except TypeError as exc:
                last_type_error = exc
                continue

        raise RuntimeError(
            f"Unsupported Gemini method signature: {last_type_error}"
        )

    # ------------------------------------------------------------------
    # FINDING NORMALIZATION / REPORT STATE
    # ------------------------------------------------------------------

    def _normalize_findings(
        self,
        findings: Sequence[dict],
        repo_name: str,
        file_path: str,
        commit_sha: Optional[str],
    ) -> List[dict]:
        normalized: List[dict] = []

        for raw in findings:
            if not isinstance(raw, dict):
                continue

            severity = normalize_text(
                raw.get("severity", "low")
            ).lower()

            if severity not in _ALLOWED_SEVERITIES:
                severity = "low"

            issue_type = normalize_text(
                raw.get("type", "bug")
            ).lower()

            if issue_type not in _ALLOWED_TYPES:
                issue_type = "bug"

            line = max(
                1,
                safe_int(raw.get("line"), 1),
            )

            title = normalize_text(
                raw.get("title")
                or raw.get("name")
                or "Code issue"
            )

            description = normalize_text(
                raw.get("description")
                or raw.get("message")
                or "Actionable code issue detected."
            )

            suggested_fix = normalize_text(
                raw.get("suggested_fix")
                or raw.get("fix")
                or "Review and correct the identified defect."
            )

            auto_fixable = safe_bool(
                raw.get("auto_fixable"),
                False,
            )

            fingerprint_source = "|".join(
                [
                    repo_name,
                    file_path,
                    str(line),
                    issue_type,
                    title.lower(),
                    description.lower(),
                ]
            )

            finding = {
                "id": sha256_text(
                    fingerprint_source
                )[:24],
                "fingerprint": sha256_text(
                    fingerprint_source
                ),
                "repository": repo_name,
                "file": file_path,
                "line": line,
                "commit_sha": commit_sha,
                "severity": severity,
                "type": issue_type,
                "title": title,
                "description": description,
                "suggested_fix": suggested_fix,
                "auto_fixable": auto_fixable,
                "detected_at": iso(utcnow()),
            }

            normalized.append(finding)

        return self._deduplicate_findings(normalized)

    def _deduplicate_findings(
        self,
        findings: Sequence[dict],
    ) -> List[dict]:
        unique: List[dict] = []
        seen: set[str] = set()

        for issue in findings:
            if not isinstance(issue, dict):
                continue

            fingerprint = issue.get("fingerprint")

            if not fingerprint:
                fingerprint = sha256_text(
                    json.dumps(
                        issue,
                        sort_keys=True,
                        default=str,
                    )
                )

            if fingerprint in seen:
                continue

            seen.add(fingerprint)
            unique.append(dict(issue))

        return unique

    def _commit_report(self, report: dict) -> None:
        issues = report.get("issues", [])

        self._last_report = json.loads(
            json.dumps(report, default=str)
        )
        self._last_issues = list(issues)
        self._last_scan_at = utcnow()

        self._scan_count += 1
        self._total_issues_found += safe_int(
            report.get("total_issues"),
            0,
        )

        self._auto_fixes_created += sum(
            1
            for issue in issues
            if issue.get("auto_fixable") is True
        )

        history_entry = {
            "scan_number": self._scan_count,
            "status": report.get("status"),
            "started_at": report.get("started_at"),
            "completed_at": report.get("completed_at"),
            "duration_seconds": report.get("duration_seconds"),
            "repos_scanned": report.get("repos_scanned", 0),
            "files_scanned": report.get("files_scanned", 0),
            "total_issues": report.get("total_issues", 0),
            "critical": report.get("critical", 0),
            "high": report.get("high", 0),
            "medium": report.get("medium", 0),
            "low": report.get("low", 0),
        }

        self._scan_history.append(history_entry)

        if len(self._scan_history) > self._max_scan_history:
            del self._scan_history[
                : len(self._scan_history) - self._max_scan_history
            ]

        for issue in issues:
            fingerprint = issue.get("fingerprint")
            if fingerprint:
                self._issue_index[fingerprint] = dict(issue)

        if len(self._issue_index) > self._max_issue_index:
            excess = len(self._issue_index) - self._max_issue_index
            for key in list(self._issue_index)[:excess]:
                self._issue_index.pop(key, None)

    # ------------------------------------------------------------------
    # EVENT BUS
    # ------------------------------------------------------------------

    def _emit_sync(
        self,
        event_name: str,
        payload: dict,
    ) -> None:
        """
        Publish an event without blocking a synchronous caller.

        Supports common EventBus APIs:
            publish(event_name, payload)
            emit(event_name, payload)
            publish(payload-with-type)
        """
        if not self._bus:
            return

        try:
            method = getattr(
                self._bus,
                "publish",
                None,
            ) or getattr(
                self._bus,
                "emit",
                None,
            )

            if not callable(method):
                return

            result = None

            try:
                result = method(event_name, payload)
            except TypeError:
                try:
                    result = method(
                        {
                            "type": event_name,
                            "event": event_name,
                            "payload": payload,
                        }
                    )
                except TypeError:
                    result = method(payload)

            if inspect.isawaitable(result):
                self._schedule_async(
                    self._await_event_result(
                        result,
                        event_name,
                    )
                )

        except Exception:
            logger.exception(
                "CodeIntel: event emission failed: %s",
                event_name,
            )

    async def _await_event_result(
        self,
        awaitable: Any,
        event_name: str,
    ) -> None:
        try:
            await awaitable
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "CodeIntel: async event publication failed: %s",
                event_name,
            )

    # ------------------------------------------------------------------
    # OPTIONAL INTEGRATIONS
    # ------------------------------------------------------------------

    async def _async_run_optional_integrations(
        self,
        report: dict,
        repos: Sequence[Any],
        issues: Sequence[dict],
    ) -> None:
        """
        Optional integration hook.

        The engine does not require a specific container API. If the
        application exposes a compatible handler, it is invoked here.
        """
        if not self._container:
            return

        candidates = (
            "handle_code_intelligence_report",
            "process_code_intelligence_report",
            "record_code_intelligence_report",
        )

        handler = None

        for name in candidates:
            candidate = getattr(
                self._container,
                name,
                None,
            )
            if callable(candidate):
                handler = candidate
                break

        if handler is None:
            return

        loop = asyncio.get_running_loop()

        try:
            result = await loop.run_in_executor(
                None,
                lambda: handler(report),
            )

            if inspect.isawaitable(result):
                await result

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "CodeIntel: optional integration failed"
            )


# ============================================================================
# SINGLETON
# ============================================================================

code_intel_engine: Optional[CodeIntelligenceEngine] = None


def init_code_intelligence(
    github_client: Any = None,
    event_bus: Any = None,
    container: Any = None,
    auto_start: bool = True,
) -> CodeIntelligenceEngine:
    """Initialize the Code Intelligence engine (sync-compatible)."""
    global code_intel_engine

    if code_intel_engine is None:
        code_intel_engine = CodeIntelligenceEngine(
            github_client=github_client,
            event_bus=event_bus,
            container=container,
        )
    else:
        if github_client is not None:
            code_intel_engine._github = github_client
        if event_bus is not None:
            code_intel_engine._bus = event_bus
        if container is not None:
            code_intel_engine._container = container

    if auto_start and not code_intel_engine._running:
        interval = safe_int(
            get_setting(
                "code_intelligence_scan_interval_hours",
                6,
            ),
            6,
        )
        code_intel_engine.start(interval)

    return code_intel_engine


def shutdown_code_intelligence() -> None:
    """Shutdown the engine (sync-compatible)."""
    global code_intel_engine

    if code_intel_engine is None:
        return

    try:
        code_intel_engine.stop()
    except Exception:
        logger.exception(
            "Code Intelligence shutdown failed"
        )


atexit.register(shutdown_code_intelligence)


__all__ = [
    "CodeIntelligenceEngine",
    "code_intel_engine",
    "init_code_intelligence",
    "shutdown_code_intelligence",
]
