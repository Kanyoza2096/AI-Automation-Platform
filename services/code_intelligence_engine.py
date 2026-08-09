"""KANYOZA CODE INTELLIGENCE ENGINE v2.0

Single-file repository intelligence engine.
Preserves the original public API while adding defensive model parsing,
scan-state correctness, chunked analysis, finding normalization/deduplication,
bounded scan history, optional remediation/Telegram/Director hooks, and
clean lifecycle management.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import re
import shutil
import tempfile
import threading
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
        except OSError:
            continue
    return files


def chunk_text(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> List[Tuple[int, str]]:
    """Split source by line boundaries and preserve approximate start lines."""
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
    VERSION = "2.0"

    def __init__(
        self,
        github_client: Any = None,
        event_bus: Any = None,
        container: Any = None,
    ) -> None:
        self._github = github_client
        self._bus = event_bus
        self._container = container

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._scan_interval_hours = 6
        self._scanning = False
        self._scan_lock = threading.Lock()
        self._state_lock = threading.RLock()

        self._last_issues: List[dict] = []
        self._last_report: Optional[dict] = None
        self._last_scan_at: Optional[datetime] = None
        self._scan_count = 0
        self._total_issues_found = 0
        self._auto_fixes_created = 0
        self._start_time = utcnow()

        self._scan_history: List[dict] = []
        self._max_scan_history = 100
        self._issue_index: Dict[str, dict] = {}
        self._max_issue_index = 10_000

        logger.info("CodeIntelligenceEngine v%s initialized", self.VERSION)

    # ------------------------------------------------------------------
    # PUBLIC STATUS API
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        with self._state_lock:
            return {
                "engine": "code_intelligence",
                "version": self.VERSION,
                "running": self._running,
                "scanning": self._scanning,
                "scan_count": self._scan_count,
                "total_issues_found": self._total_issues_found,
                "auto_fixes_created": self._auto_fixes_created,
                "last_scan_at": iso(self._last_scan_at),
                "last_report": dict(self._last_report) if self._last_report else None,
                "uptime_hours": round(
                    (utcnow() - self._start_time).total_seconds() / 3600, 2
                ),
                "history_count": len(self._scan_history),
            }

    def get_issues(
        self,
        severity: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        limit = max(1, min(safe_int(limit, 100), 1000))
        with self._state_lock:
            issues = list(self._last_issues)

        if severity:
            severity = severity.strip().lower()
            issues = [i for i in issues if i.get("severity") == severity]

        return issues[:limit]

    def get_report(self) -> Optional[dict]:
        with self._state_lock:
            if not self._last_report:
                return None
            return json.loads(json.dumps(self._last_report, default=str))

    def get_scan_history(self, limit: int = 20) -> List[dict]:
        limit = max(1, min(safe_int(limit, 20), self._max_scan_history))
        with self._state_lock:
            return list(reversed(self._scan_history[-limit:]))

    # ------------------------------------------------------------------
    # LIFECYCLE
    # ------------------------------------------------------------------

    def start(self, interval_hours: int = 6) -> None:
        with self._state_lock:
            if self._running:
                return
            self._running = True
            self._scan_interval_hours = max(1, safe_int(interval_hours, 6))
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._scan_loop,
                daemon=True,
                name="code-intel-engine",
            )
            self._thread.start()

        logger.info(
            "CodeIntelligenceEngine started (every %dh)",
            self._scan_interval_hours,
        )
        self._emit(
            "code_intelligence_started",
            {"version": self.VERSION, "interval_hours": self._scan_interval_hours},
        )

    def stop(self) -> None:
        with self._state_lock:
            self._running = False
            self._stop_event.set()
            thread = self._thread

        if thread and thread.is_alive():
            thread.join(timeout=10)

        with self._state_lock:
            self._thread = None

        logger.info("CodeIntelligenceEngine stopped")
        self._emit("code_intelligence_stopped", {"version": self.VERSION})

    def _scan_loop(self) -> None:
        if self._stop_event.wait(60):
            return

        if self._running:
            try:
                self.scan_now()
            except Exception:
                logger.exception("CodeIntel: initial scheduled scan failed")

        interval_seconds = self._scan_interval_hours * 3600
        while self._running and not self._stop_event.wait(interval_seconds):
            try:
                self.scan_now()
            except Exception:
                logger.exception("CodeIntel: scheduled scan failed")

    # ------------------------------------------------------------------
    # SCANNING
    # ------------------------------------------------------------------

    def scan_now(self) -> dict:
        if not self._scan_lock.acquire(blocking=False):
            return {
                "status": "skipped",
                "reason": "scan_in_progress",
                "total_issues": 0,
            }

        started = utcnow()
        with self._state_lock:
            self._scanning = True

        temp_dir: Optional[str] = None

        self._emit(
            "code_intelligence_scan_started",
            {"started_at": iso(started), "scan_number": self._scan_count + 1},
        )

        try:
            temp_dir = tempfile.mkdtemp(prefix="code_intel_")
            return self._run_full_scan(temp_dir, started)

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
                    (completed - started).total_seconds(), 3
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

            with self._state_lock:
                self._last_report = report
                self._last_scan_at = completed
                self._record_scan_history(report)

            self._emit("code_intelligence_scan_failed", report)
            return report

        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

            with self._state_lock:
                self._scanning = False

            self._scan_lock.release()

    def _run_full_scan(self, temp_dir: str, scan_started: datetime) -> dict:
        if not self._github:
            raise RuntimeError("GitHub client not configured")

        self._validate_github_client()
        repos = self._get_repositories()

        if not repos:
            completed = utcnow()
            report = {
                "scan_type": "code_intelligence",
                "engine_version": self.VERSION,
                "status": "completed",
                "started_at": iso(scan_started),
                "completed_at": iso(completed),
                "duration_seconds": round(
                    (completed - scan_started).total_seconds(), 3
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
            self._emit("code_intelligence_scan_completed", report)
            return report

        all_issues: List[dict] = []
        files_scanned = 0
        repos_with_issues = 0
        repo_results: List[dict] = []

        for repo in repos:
            if self._stop_event.is_set():
                break

            repo_name = self._repo_name(repo)

            try:
                local_path = self._clone_repository(repo, temp_dir)
                if not local_path:
                    repo_results.append({
                        "repository": repo_name,
                        "status": "failed",
                        "error": "repository_clone_returned_empty_path",
                        "issues": 0,
                    })
                    continue

                repo_issues, file_count = self._scan_repository(
                    repo_name, Path(local_path), repo
                )
                files_scanned += file_count

                if repo_issues:
                    repos_with_issues += 1
                    all_issues.extend(repo_issues)

                repo_results.append({
                    "repository": repo_name,
                    "status": "completed",
                    "files_scanned": file_count,
                    "issues": len(repo_issues),
                    "commit_sha": self._repo_commit_sha(repo),
                })

            except Exception as exc:
                logger.exception(
                    "CodeIntel: failed to scan repository %s", repo_name
                )
                repo_results.append({
                    "repository": repo_name,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "issues": 0,
                })

        all_issues = self._deduplicate_findings(all_issues)

        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for issue in all_issues:
            severity = issue.get("severity", "low")
            if severity in counts:
                counts[severity] += 1

        completed = utcnow()
        status = "cancelled" if self._stop_event.is_set() else "completed"

        report = {
            "scan_type": "code_intelligence",
            "engine_version": self.VERSION,
            "status": status,
            "started_at": iso(scan_started),
            "completed_at": iso(completed),
            "duration_seconds": round(
                (completed - scan_started).total_seconds(), 3
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
            self._run_optional_integrations(report, repos, all_issues)

        self._emit(
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
            len(repos), files_scanned, len(all_issues),
            counts["critical"], counts["high"],
        )

        return report

    # ------------------------------------------------------------------
    # GITHUB
    # ------------------------------------------------------------------

    def _validate_github_client(self) -> None:
        validator = getattr(self._github, "validate_token", None)
        if not callable(validator):
            logger.warning(
                "CodeIntel: GitHub client has no validate_token(); "
                "continuing with available capabilities"
            )
            return
        if validator() is False:
            raise RuntimeError("invalid_github_token")

    def _get_repositories(self) -> List[Any]:
        getter = getattr(self._github, "get_all_repos", None)
        if not callable(getter):
            raise RuntimeError(
                "GitHub client does not expose get_all_repos()"
            )

        excluded = get_setting("guardian_exclude_repos", []) or []
        try:
            repos = getter(exclude=excluded)
        except TypeError:
            repos = getter()

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
    def _repo_commit_sha(repo: Any) -> Optional[str]:
        if isinstance(repo, dict):
            for key in (
                "commit_sha", "sha", "default_branch_sha", "head_sha"
            ):
                if repo.get(key):
                    return str(repo[key])

        for attr in (
            "commit_sha", "sha", "default_branch_sha", "head_sha"
        ):
            value = getattr(repo, attr, None)
            if value:
                return str(value)

        return None

    def _clone_repository(self, repo: Any, temp_dir: str) -> Optional[str]:
        clone = getattr(self._github, "clone_or_pull", None)
        if not callable(clone):
            raise RuntimeError(
                "GitHub client does not expose clone_or_pull()"
            )
        return clone(repo, temp_dir)

    # ------------------------------------------------------------------
    # REPOSITORY ANALYSIS
    # ------------------------------------------------------------------

    def _scan_repository(
        self,
        repo_name: str,
        local_path: Path,
        repo: Any,
    ) -> Tuple[List[dict], int]:
        issues: List[dict] = []
        files = iter_candidate_files(local_path)

        for file_path in files:
            if self._stop_event.is_set():
                break

            try:
                relative_path = str(file_path.relative_to(local_path))
                code = file_path.read_text(
                    encoding="utf-8", errors="ignore"
                )

                if len(code.strip()) < 10:
                    continue

                issues.extend(
                    self._analyze_file(
                        repo_name,
                        relative_path,
                        code,
                        repo,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "CodeIntel: failed to analyze %s: %s",
                    file_path, exc,
                )

        return self._deduplicate_findings(issues), len(files)

    def _analyze_file(
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
                results.extend(
                    self._analyze_with_gemini(
                        repo=repo_name,
                        file_path=file_path,
                        code=chunk,
                        start_line=start_line,
                        chunk_index=chunk_index,
                        total_chunks=len(chunks),
                        commit_sha=self._repo_commit_sha(repo),
                    )
                )
            except Exception as exc:
                logger.warning(
                    "CodeIntel: Gemini analysis failed for %s/%s: %s",
                    repo_name, file_path, exc,
                )

        return self._normalize_findings(
            results,
            repo_name,
            file_path,
            self._repo_commit_sha(repo),
        )

    # ------------------------------------------------------------------
    # GEMINI
    # ------------------------------------------------------------------

    def _analyze_with_gemini(
        self,
        repo: str,
        file_path: str,
        code: str,
        start_line: int = 1,
        chunk_index: int = 0,
        total_chunks: int = 1,
        commit_sha: Optional[str] = None,
    ) -> List[dict]:
        try:
            from services.gemini import gemini
        except Exception as exc:
            raise RuntimeError(
                f"Gemini service unavailable: {exc}"
            ) from exc

        prompt = self._build_analysis_prompt(
            repo, file_path, code, start_line,
            chunk_index, total_chunks, commit_sha,
        )

        raw_response = self._call_gemini(gemini, prompt)
        payload = extract_json_payload(raw_response)

        if payload is None:
            logger.warning(
                "CodeIntel: Gemini returned non-JSON output for %s/%s",
                repo, file_path,
            )
            return []

        if isinstance(payload, dict):
            payload = payload.get("issues", [])

        if not isinstance(payload, list):
            return []

        return [item for item in payload if isinstance(item, dict)]

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
        extension = Path(file_path).suffix.lower().lstrip(".")

        return f"""
You are the Code Intelligence component of a production software platform.

Perform a strict senior-engineer security and correctness review.

Repository: {repo}
Commit SHA: {commit_sha or "unknown"}
File: {file_path}
Chunk: {chunk_index + 1}/{total_chunks}
Approximate starting line: {start_line}

Only report REAL, ACTIONABLE problems supported by the supplied code.

DO NOT report:
- formatting preferences
- naming preferences
- missing docstrings
- missing type hints
- generic refactoring suggestions
- hypothetical issues without evidence

DO report:
- security vulnerabilities
- authentication/authorization flaws
- SQL injection
- XSS
- command injection
- hardcoded secrets
- concrete logic bugs
- incorrect state transitions
- race conditions
- unsafe concurrency
- blocking calls in async contexts
- resource leaks
- incorrect exception handling
- API contract violations visible from this code
- deprecated/removed API usage
- configuration errors
- dangerous retry behavior
- duplicate execution risks
- data corruption risks
- concrete performance problems

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

    @staticmethod
    def _call_gemini(client: Any, prompt: str) -> Any:
        for method_name in _GEMINI_METHODS:
            method = getattr(client, method_name, None)
            if not callable(method):
                continue

            try:
                return method(prompt)
            except TypeError:
                for kwargs in (
                    {"prompt": prompt},
                    {"content": prompt},
                    {"text": prompt},
                    {"message": prompt},
                ):
                    try:
                        return method(**kwargs)
                    except TypeError:
                        continue

        raise RuntimeError(
            "Gemini client exposes none of the supported generation methods: "
            + ", ".join(_GEMINI_METHODS)
        )

    # ------------------------------------------------------------------
    # FINDINGS
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

            line = max(0, safe_int(raw.get("line"), 0))
            title = normalize_text(
                raw.get("title") or "Code issue detected"
            )
            description = normalize_text(raw.get("description"))
            suggested_fix = normalize_text(raw.get("suggested_fix"))

            if not description:
                continue

            source = "|".join([
                repo_name,
                commit_sha or "",
                file_path,
                str(line),
                issue_type,
                title.lower(),
                description.lower(),
            ])
            fingerprint = sha256_text(source)

            normalized.append({
                "id": f"issue_{fingerprint[:16]}",
                "fingerprint": fingerprint,
                "repository": repo_name,
                "commit_sha": commit_sha,
                "file": file_path,
                "line": line,
                "severity": severity,
                "type": issue_type,
                "title": title,
                "description": description,
                "suggested_fix": suggested_fix,
                "auto_fixable": safe_bool(
                    raw.get("auto_fixable"), False
                ),
                "status": "open",
                "detected_at": iso(utcnow()),
            })

        return normalized

    def _deduplicate_findings(
        self,
        findings: Sequence[dict],
    ) -> List[dict]:
        unique: Dict[str, dict] = {}

        for issue in findings:
            fingerprint = issue.get("fingerprint")
            if not fingerprint:
                fingerprint = sha256_text(
                    json.dumps(issue, sort_keys=True, default=str)
                )

            if fingerprint not in unique:
                unique[fingerprint] = issue

        result = list(unique.values())

        with self._state_lock:
            for issue in result:
                fingerprint = issue.get("fingerprint")
                if fingerprint:
                    self._issue_index[fingerprint] = issue

            if len(self._issue_index) > self._max_issue_index:
                excess = len(self._issue_index) - self._max_issue_index
                for key in list(self._issue_index)[:excess]:
                    del self._issue_index[key]

        return result

    # ------------------------------------------------------------------
    # STATE
    # ------------------------------------------------------------------

    def _commit_report(self, report: dict) -> None:
        issues = report.get("issues", [])

        with self._state_lock:
            self._last_issues = list(issues)
            self._last_report = json.loads(
                json.dumps(report, default=str)
            )

            completed_at = report.get("completed_at")
            try:
                self._last_scan_at = (
                    datetime.fromisoformat(completed_at)
                    if completed_at else utcnow()
                )
            except Exception:
                self._last_scan_at = utcnow()

            if report.get("status") in {"completed", "cancelled"}:
                self._scan_count += 1

            self._total_issues_found += len(issues)
            self._record_scan_history(report)

    def _record_scan_history(self, report: dict) -> None:
        self._scan_history.append({
            key: value
            for key, value in report.items()
            if key != "issues"
        })

        if len(self._scan_history) > self._max_scan_history:
            del self._scan_history[
                :len(self._scan_history) - self._max_scan_history
            ]

    # ------------------------------------------------------------------
    # OPTIONAL INTEGRATIONS
    # ------------------------------------------------------------------

    def _run_optional_integrations(
        self,
        report: dict,
        repos: Sequence[Any],
        issues: Sequence[dict],
    ) -> None:
        for name, callback, args in (
            ("auto-fix", self._auto_fix_issues, (repos, issues)),
            ("telegram", self._send_telegram_report, (report,)),
            ("director", self._notify_director, (report,)),
        ):
            try:
                callback(*args)
            except Exception:
                logger.exception(
                    "CodeIntel: optional %s stage failed", name
                )

    def _auto_fix_issues(
        self,
        repos: Sequence[Any],
        issues: Sequence[dict],
    ) -> dict:
        fixable = [
            issue for issue in issues
            if issue.get("auto_fixable") is True
        ]

        if not fixable:
            return {
                "attempted": 0,
                "created": 0,
                "status": "nothing_to_fix",
            }

        handler = self._resolve_capability(
            self._container,
            (
                "auto_fix_code_issues",
                "apply_code_fixes",
                "create_code_fix",
            ),
        )

        if not handler:
            logger.info(
                "CodeIntel: %d findings marked auto-fixable but "
                "no remediation adapter is configured",
                len(fixable),
            )
            return {
                "attempted": 0,
                "created": 0,
                "status": "no_remediation_adapter",
            }

        created = 0
        for issue in fixable:
            try:
                if handler(issue):
                    created += 1
            except Exception:
                logger.exception(
                    "CodeIntel: auto-fix failed for %s",
                    issue.get("id"),
                )

        with self._state_lock:
            self._auto_fixes_created += created

        return {
            "attempted": len(fixable),
            "created": created,
            "status": "completed",
        }

    def _send_telegram_report(self, report: dict) -> bool:
        try:
            from services.email_service import email_service
        except Exception:
            return False

        sender = getattr(email_service, "send_telegram", None)
        if not callable(sender):
            return False

        critical = report.get("critical", 0)
        high = report.get("high", 0)

        if critical == 0 and high == 0:
            return False

        message = (
            "🧠 <b>KANYOZA CODE INTELLIGENCE</b>\n"
            f"Status: {report.get('status')}\n"
            f"Repositories: {report.get('repos_scanned', 0)}\n"
            f"Files: {report.get('files_scanned', 0)}\n"
            f"Critical: {critical}\n"
            f"High: {high}\n"
            f"Medium: {report.get('medium', 0)}\n"
            f"Low: {report.get('low', 0)}\n"
            f"Time: {utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        )

        sender(message)
        return True

    def _notify_director(self, report: dict) -> bool:
        payload = {
            "source": "code_intelligence",
            "event": "scan_completed",
            "status": report.get("status"),
            "total_issues": report.get("total_issues", 0),
            "critical": report.get("critical", 0),
            "high": report.get("high", 0),
            "medium": report.get("medium", 0),
            "low": report.get("low", 0),
            "repos_scanned": report.get("repos_scanned", 0),
            "files_scanned": report.get("files_scanned", 0),
            "completed_at": report.get("completed_at"),
        }

        emitted = self._emit("code_intelligence_report", payload)

        director = self._resolve_capability(
            self._container,
            (
                "notify_director",
                "handle_code_intelligence_report",
                "on_code_intelligence_report",
            ),
        )

        if director:
            director(report)
            return True

        return emitted

    # ------------------------------------------------------------------
    # EVENT BUS
    # ------------------------------------------------------------------

    def _emit(
        self,
        event_name: str,
        payload: Dict[str, Any],
    ) -> bool:
        if not self._bus:
            return False

        try:
            emitter = getattr(self._bus, "emit", None)
            if callable(emitter):
                emitter(event_name, payload)
                return True

            publisher = getattr(self._bus, "publish", None)
            if callable(publisher):
                publisher(event_name, payload)
                return True
        except Exception:
            logger.exception(
                "CodeIntel: event emission failed: %s",
                event_name,
            )

        return False

    @staticmethod
    def _resolve_capability(
        owner: Any,
        names: Sequence[str],
    ) -> Optional[Any]:
        if owner is None:
            return None

        for name in names:
            candidate = getattr(owner, name, None)
            if callable(candidate):
                return candidate

        for registry_name in (
            "services",
            "engines",
            "industry_plugins",
        ):
            registry = getattr(owner, registry_name, None)
            if not isinstance(registry, dict):
                continue

            for name in names:
                candidate = registry.get(name)
                if callable(candidate):
                    return candidate

                if candidate is not None:
                    for method_name in (
                        "handle", "run", "execute", "notify"
                    ):
                        method = getattr(
                            candidate, method_name, None
                        )
                        if callable(method):
                            return method

        return None


# ----------------------------------------------------------------------
# SINGLETON / INITIALIZATION
# ----------------------------------------------------------------------

code_intel_engine: Optional[CodeIntelligenceEngine] = None


def init_code_intelligence(
    github_client: Any = None,
    event_bus: Any = None,
    container: Any = None,
    auto_start: bool = True,
) -> CodeIntelligenceEngine:
    global code_intel_engine

    if code_intel_engine is None:
        code_intel_engine = CodeIntelligenceEngine(
            github_client=github_client,
            event_bus=event_bus,
            container=container,
        )
    else:
        code_intel_engine._github = (
            github_client or code_intel_engine._github
        )
        code_intel_engine._bus = (
            event_bus or code_intel_engine._bus
        )
        code_intel_engine._container = (
            container or code_intel_engine._container
        )

    if auto_start and not code_intel_engine._running:
        code_intel_engine.start()

    return code_intel_engine


def shutdown_code_intelligence() -> None:
    if code_intel_engine is None:
        return

    try:
        code_intel_engine.stop()
    except Exception:
        logger.exception("Code Intelligence shutdown failed")


atexit.register(shutdown_code_intelligence)
