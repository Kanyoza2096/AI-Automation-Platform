"""
M9 — Code Guardian scanner unit tests.

These tests do NOT require GitHub credentials.  They test the scanner's
pattern-matching and report-building logic directly.
"""

from __future__ import annotations

import pytest

# Avoid importing the real scanner if guardian is not installed
guardian = pytest.importorskip("guardian.scanner", reason="guardian module not available")


class TestCodeGuardianScanner:
    """Unit tests for CodeGuardianScanner pattern matching."""

    @pytest.fixture()
    def scanner(self):
        from guardian.scanner import CodeGuardianScanner
        return CodeGuardianScanner()

    def test_scanner_instantiates(self, scanner):
        from guardian.scanner import CodeGuardianScanner
        assert isinstance(scanner, CodeGuardianScanner)

    def test_build_report_empty(self, scanner):
        report = scanner.build_report([], repos_count=0, files_count=0)
        assert report["total_issues"] == 0
        assert report["repos_scanned"] == 0
        assert report["files_scanned"] == 0

    def test_build_report_with_issues(self, scanner):
        # Minimal fake issue objects that have a .severity attribute
        class _FakeIssue:
            severity = "high"
            repo = "test-repo"
            file = "test.py"
            line = 1
            issue_type = "hardcoded_secret"
            message = "Found hardcoded secret"
            context = ""
            fixable = False

        issues = [_FakeIssue()]
        report = scanner.build_report(issues, repos_count=1, files_count=5)
        assert report["total_issues"] == 1
        assert report["high"] == 1
        assert report["repos_scanned"] == 1
        assert report["files_scanned"] == 5

    def test_build_report_severity_counts(self, scanner):
        class _FakeIssue:
            file = "x.py"
            line = 1
            issue_type = "test"
            message = "test"
            context = ""
            fixable = False
            repo = "r"

        class Critical(_FakeIssue):
            severity = "critical"

        class Medium(_FakeIssue):
            severity = "medium"

        class Low(_FakeIssue):
            severity = "low"

        issues = [Critical(), Medium(), Low()]
        report = scanner.build_report(issues, repos_count=1, files_count=10)
        assert report["critical"] == 1
        assert report["high"] == 0
        assert report["medium"] == 1
        assert report["low"] == 1
        assert report["total_issues"] == 3
