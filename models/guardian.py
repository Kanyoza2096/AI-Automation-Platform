"""
Code Guardian domain models — repository issues and scan reports.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import Field

from models.base import KanyozaModel


class IssueSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class IssueCategory(str, Enum):
    SECURITY = "security"
    DEPENDENCY = "dependency"
    CODE_QUALITY = "code_quality"
    PERFORMANCE = "performance"
    TYPE_SAFETY = "type_safety"
    DOCUMENTATION = "documentation"
    TESTING = "testing"
    STYLE = "style"


class RepositoryIssue(KanyozaModel):
    """A single code issue found during a Guardian scan."""

    repo: str = Field(..., description="Repository name")
    repo_full: str = Field(..., description="owner/repo format")
    file_path: str = Field(default="", description="Relative path within repo")
    line_number: Optional[int] = Field(default=None)
    severity: IssueSeverity
    category: IssueCategory
    rule: str = Field(..., description="Rule or check identifier")
    message: str = Field(..., description="Human-readable issue description")
    code_snippet: Optional[str] = Field(default=None)

    # AI enrichment (populated after AI explanation pass)
    ai_explanation: Optional[str] = Field(default=None)
    ai_fix_suggestion: Optional[str] = Field(default=None)
    auto_fixable: bool = Field(default=False)

    # Resolution tracking
    fixed: bool = Field(default=False)
    pr_url: Optional[str] = Field(default=None)
    detected_at: datetime = Field(default_factory=KanyozaModel.utcnow)

    @property
    def location(self) -> str:
        if self.line_number:
            return f"{self.file_path}:{self.line_number}"
        return self.file_path or self.repo


class GuardianReport(KanyozaModel):
    """
    Aggregated report from a single Code Guardian scan run.
    Contains all discovered issues and summary metrics.
    """

    scan_id: str = Field(..., description="Unique identifier for this scan run")
    repos_scanned: int = Field(default=0, ge=0)
    files_scanned: int = Field(default=0, ge=0)
    issues: List[RepositoryIssue] = Field(default_factory=list)

    # Aggregated counts by severity
    critical: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    medium: int = Field(default=0, ge=0)
    low: int = Field(default=0, ge=0)

    # Auto-fix results
    auto_fixed: int = Field(default=0, ge=0)
    prs_created: int = Field(default=0, ge=0)

    scan_started_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    scan_completed_at: Optional[datetime] = None
    error: Optional[str] = None

    @property
    def total_issues(self) -> int:
        return len(self.issues)

    @property
    def is_clean(self) -> bool:
        return self.total_issues == 0

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.scan_completed_at:
            return (self.scan_completed_at - self.scan_started_at).total_seconds()
        return None

    @classmethod
    def empty(cls, scan_id: str) -> "GuardianReport":
        return cls(scan_id=scan_id)

    def summary_text(self) -> str:
        now = self.scan_completed_at or KanyozaModel.utcnow()
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        if self.is_clean:
            return (
                f"🛡️ Code Guardian Report\n\n"
                f"📅 {ts}\n"
                f"📂 {self.repos_scanned} repos | {self.files_scanned} files\n\n"
                f"✅ ALL CLEAR"
            )
        return (
            f"🛡️ Code Guardian Report\n\n"
            f"📅 {ts}\n"
            f"📂 {self.repos_scanned} repos | {self.files_scanned} files\n\n"
            f"🔴 Critical: {self.critical}  🟠 High: {self.high}  🟡 Medium: {self.medium}\n"
            f"⚠️ {self.total_issues} issues found"
        )
