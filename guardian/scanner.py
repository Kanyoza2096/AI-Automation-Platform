"""
Code Guardian — multi-language static security scanner.
Scans cloned repositories for common security issues.
"""

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger("kanyoza.guardian.scanner")

# ---------------------------------------------------------------------------
# Issue types
# ---------------------------------------------------------------------------

@dataclass
class SecurityIssue:
    severity: str
    rule_id: str
    description: str
    file_path: str
    line_number: int
    snippet: str
    suggestion: str = ""
    repo_name: str = ""   # set by scan_repos() so filtering/auto-fix works

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.lower(),
            "rule_id": self.rule_id,
            "description": self.description,
            # ── aliases expected by AutoFixEngine & owner commands ──────
            "message": self.description,
            "recommendation": self.suggestion,
            "repo": self.repo_name,
            # ── original fields ─────────────────────────────────────────
            "file": self.file_path,
            "line": self.line_number,
            "snippet": self.snippet[:200],
            "suggestion": self.suggestion,
        }


@dataclass
class ScanResult:
    repo_name: str
    scanned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    files_scanned: int = 0
    issues: List[SecurityIssue] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity.upper() == "CRITICAL")

    def to_dict(self) -> dict:
        return {
            "repo": self.repo_name,
            "scanned_at": self.scanned_at.isoformat(),
            "files_scanned": self.files_scanned,
            "issue_count": self.issue_count,
            "critical_count": self.critical_count,
            "issues": [i.to_dict() for i in self.issues[:50]],
            "error": self.error,
        }

    def summary_text(self) -> str:
        if self.error:
            return f"❌ {self.repo_name}: scan error — {self.error}"
        severity_counts: Dict[str, int] = {}
        for issue in self.issues:
            sev = issue.severity.upper()
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
        if not severity_counts:
            return f"✅ {self.repo_name}: clean ({self.files_scanned} files)"
        parts = [f"{v} {k}" for k, v in sorted(severity_counts.items())]
        return f"⚠️ {self.repo_name}: {', '.join(parts)} ({self.files_scanned} files)"


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    rule_id: str
    severity: str
    description: str
    pattern: str
    extensions: List[str]
    suggestion: str
    _compiled: re.Pattern = field(init=False, repr=False)

    def __post_init__(self):
        self._compiled = re.compile(self.pattern, re.IGNORECASE)

    def matches(self, line: str) -> bool:
        return bool(self._compiled.search(line))


RULES: List[Rule] = [
    Rule("SEC001", "CRITICAL", "Hardcoded API key or token",
         r"(api[_-]?key|api[_-]?token|access[_-]?token)\s*=\s*['\"][a-zA-Z0-9_\-]{20,}['\"]",
         [".py", ".js", ".ts", ".go", ".rb", ".php", ".java"],
         "Use environment variables for all secrets"),
    Rule("SEC002", "CRITICAL", "Hardcoded password",
         r"(password|passwd|pwd)\s*=\s*['\"][^'\"]{6,}['\"]",
         [".py", ".js", ".ts", ".go", ".rb", ".php", ".java", ".yml", ".yaml", ".json"],
         "Use a secrets manager or environment variables"),
    Rule("SEC003", "HIGH", "Hardcoded private key or secret",
         r"-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----",
         [".py", ".js", ".ts", ".pem", ".key", ".env"],
         "Never commit private keys to source control"),
    Rule("SEC004", "HIGH", "SQL injection risk",
         r"(execute|cursor\.execute|db\.query|raw_query)\s*\(\s*[f'\"].*?\%s.*?['\"]",
         [".py"],
         "Use parameterized queries / ORM methods"),
    Rule("SEC005", "HIGH", "eval() usage",
         r"\beval\s*\(",
         [".py", ".js", ".ts", ".rb"],
         "Avoid eval() — it executes arbitrary code"),
    Rule("SEC006", "HIGH", "Shell injection risk",
         r"(os\.system|subprocess\.call|subprocess\.run|popen)\s*\(.*?\+",
         [".py"],
         "Use subprocess with a list of args, never string concatenation"),
    Rule("SEC007", "MEDIUM", "Debug mode enabled in production",
         r"DEBUG\s*=\s*True",
         [".py", ".env"],
         "Disable DEBUG in production"),
    Rule("SEC008", "MEDIUM", "Insecure SSL verification disabled",
         r"verify\s*=\s*False",
         [".py"],
         "Never disable SSL verification in production"),
    Rule("SEC009", "MEDIUM", "print() used for logging",
         r"^\s*print\s*\(",
         [".py"],
         "Use structured logging instead of print()"),
    Rule("SEC010", "MEDIUM", "Bare except clause",
         r"^\s*except\s*:",
         [".py"],
         "Use 'except Exception:' to avoid swallowing system exits"),
    Rule("SEC011", "LOW", "TODO/FIXME security comment",
         r"(TODO|FIXME|HACK|XXX).*(security|auth|secret|password|token|vuln)",
         [".py", ".js", ".ts", ".go"],
         "Address security TODOs before deploying"),
    Rule("SEC012", "LOW", "Hardcoded localhost URL",
         r"http://localhost:[0-9]+",
         [".py", ".js", ".ts"],
         "Use environment variables for service URLs"),
    Rule("SEC013", "CRITICAL", "AWS/GCP credentials exposed",
         r"(AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{35})",
         [".py", ".js", ".ts", ".yml", ".yaml", ".json", ".env"],
         "Rotate this credential immediately"),
]

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env",
              "dist", "build", ".next", "target", "vendor"}
_MAX_FILE_SIZE = 500 * 1024


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class CodeGuardianScanner:
    """Multi-language security scanner."""

    def __init__(self, github_client=None, analytics=None, supabase_client=None):
        self._github = github_client
        self._analytics = analytics
        self._sb = supabase_client
        self._last_results: List[ScanResult] = []

    # ------------------------------------------------------------------
    # Main API (called by bot.py)
    # ------------------------------------------------------------------

    def scan_repos(self, repo_paths: List[dict]) -> List:
        """
        Scan multiple repositories. Called by bot.py.
        repo_paths: [{"name": str, "local_path": str}, ...]
        Returns list of SecurityIssue objects, each tagged with repo_name.
        """
        all_issues = []
        for rp in repo_paths:
            result = self.scan_directory(rp["local_path"], rp["name"])
            # Tag every issue with the repo name so AutoFixEngine can filter by repo
            for issue in result.issues:
                issue.repo_name = rp["name"]
            all_issues.extend(result.issues)
            self._last_results.append(result)
        return all_issues

    def build_report(self, issues: list, repos_count: int, files_count: int) -> dict:
        """Build a summary report for bot.py's Guardian scan flow."""
        critical = sum(1 for i in issues if _sev(i) == "critical")
        high = sum(1 for i in issues if _sev(i) == "high")
        medium = sum(1 for i in issues if _sev(i) == "medium")
        low = sum(1 for i in issues if _sev(i) == "low")

        return {
            "repos_scanned": repos_count,
            "files_scanned": files_count,
            "total_issues": len(issues),
            "critical": critical,
            "high": high,
            "medium": medium,
            "low": low,
            "issues": [i.to_dict() if hasattr(i, 'to_dict') else i for i in issues],
        }

    def scan_all_repos(self, exclude: Optional[List[str]] = None) -> List[ScanResult]:
        """Fetch all repos, clone, scan, return results."""
        if not self._github:
            logger.warning("GitHub client not configured")
            return []
        if not self._github.validate_token():
            logger.error("GitHub token invalid")
            return []

        repos = self._github.get_all_repos(exclude=exclude)
        if not repos:
            logger.info("No repositories to scan")
            return []

        results: List[ScanResult] = []
        total_issues = 0

        with tempfile.TemporaryDirectory(prefix="guardian_") as tmp_dir:
            for repo in repos:
                try:
                    local_path = self._github.clone_or_pull(repo, tmp_dir)
                    if not local_path:
                        result = ScanResult(repo_name=repo["name"], error="clone_failed")
                    else:
                        result = self.scan_directory(local_path, repo_name=repo["name"])
                    results.append(result)
                    total_issues += result.issue_count
                    logger.info(result.summary_text())
                except Exception as exc:
                    logger.error("Scan failed for %s: %s", repo["name"], exc)
                    results.append(ScanResult(repo_name=repo["name"], error=str(exc)))

        self._last_results = results

        if self._analytics:
            self._analytics.record_guardian_scan(issues=total_issues)

        return results

    def scan_directory(self, directory: str, repo_name: str = "") -> ScanResult:
        """Scan all source files in a directory tree."""
        result = ScanResult(repo_name=repo_name or os.path.basename(directory))

        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if not any(r.extensions and ext in r.extensions for r in RULES):
                    continue
                file_path = os.path.join(root, filename)
                rel_path = os.path.relpath(file_path, directory)
                try:
                    issues = self._scan_file(file_path, rel_path)
                    result.issues.extend(issues)
                    result.files_scanned += 1
                except Exception as exc:
                    logger.debug("Could not scan %s: %s", rel_path, exc)

        return result

    def _scan_file(self, file_path: str, rel_path: str) -> List[SecurityIssue]:
        """Apply all rules to a single file."""
        size = os.path.getsize(file_path)
        if size > _MAX_FILE_SIZE:
            return []

        ext = os.path.splitext(file_path)[1].lower()
        applicable_rules = [r for r in RULES if not r.extensions or ext in r.extensions]
        if not applicable_rules:
            return []

        issues: List[SecurityIssue] = []
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, start=1):
                    for rule in applicable_rules:
                        if rule.matches(line):
                            issues.append(SecurityIssue(
                                severity=rule.severity,
                                rule_id=rule.rule_id,
                                description=rule.description,
                                file_path=rel_path,
                                line_number=lineno,
                                snippet=line.strip()[:200],
                                suggestion=rule.suggestion,
                            ))
        except Exception as exc:
            logger.debug("File read error for %s: %s", rel_path, exc)

        return issues

    def get_last_results(self) -> List[ScanResult]:
        return list(self._last_results)

    def format_report(self, results: List[ScanResult]) -> str:
        """Human-readable scan summary."""
        if not results:
            return "🔍 Code Guardian: No repositories scanned."

        lines = [
            f"🛡️ Code Guardian Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            f"Repos scanned: {len(results)}",
            "",
        ]
        total_issues = sum(r.issue_count for r in results)
        critical = sum(r.critical_count for r in results)
        lines.append(f"Total issues: {total_issues}  (🔴 Critical: {critical})")
        lines.append("")

        for result in sorted(results, key=lambda r: r.issue_count, reverse=True)[:10]:
            lines.append(result.summary_text())

        if critical > 0:
            lines.append("\n⚠️ Critical issues found! Review and rotate exposed credentials immediately.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sev(issue) -> str:
    """Get normalized severity string from an issue dict or object."""
    if isinstance(issue, dict):
        return issue.get("severity", "low").lower()
    return getattr(issue, "severity", "low").lower()
