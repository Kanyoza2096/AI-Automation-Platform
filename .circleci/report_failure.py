#!/usr/bin/env python3
"""Publish a redacted CircleCI failure summary to GitHub when configured."""
from __future__ import annotations
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

LOG_FILES = (
    ("Critical lint", Path("/tmp/kdo-critical-lint.log")),
    ("Unit and architecture tests", Path("/tmp/kdo-unit-tests.log")),
    ("Mock integration tests", Path("/tmp/kdo-integration-tests.log")),
)
MAX_SECTION_CHARS = 12000
MAX_BODY_CHARS = 45000

def redact(value: str) -> str:
    patterns = (
        (r"(?i)(authorization\\s*[:=]\\s*bearer\\s+)[^\\s]+", r"\\1[REDACTED]"),
        (r"(?i)((?:api[_-]?key|access[_-]?token|secret|password)\\s*[:=]\\s*)[^\\s,;]+", r"\\1[REDACTED]"),
        (r"(?i)(gh[pousr]_[A-Za-z0-9_]{20,})", "[REDACTED_GITHUB_TOKEN]"),
        (r"(?i)(sk-[A-Za-z0-9_-]{20,})", "[REDACTED_API_KEY]"),
    )
    for pattern, replacement in patterns:
        value = re.sub(pattern, replacement, value)
    return value

def main() -> int:
    branch = os.getenv("CIRCLE_BRANCH", "unknown")
    build_url = os.getenv("CIRCLE_BUILD_URL", "")
    build_num = os.getenv("CIRCLE_BUILD_NUM", "unknown")
    sha = os.getenv("CIRCLE_SHA1", "unknown")
    owner = os.getenv("CIRCLE_PROJECT_USERNAME", "Kanyoza2096")
    repo = os.getenv("CIRCLE_PROJECT_REPONAME", "AI-Automation-Platform")
    sections = []
    for label, path in LOG_FILES:
        if path.exists():
            output = redact(path.read_text(errors="replace"))[-MAX_SECTION_CHARS:]
            if output.strip():
                sections.append("### " + label + "\\n\\n" + output)
    if not sections:
        sections.append("No captured step output was found. Open the CircleCI job URL for details.")
    body = (
        "Automated CircleCI failure report.\\n\\n"
        + "- **Branch:** " + branch + "\\n"
        + "- **Commit:** " + sha + "\\n"
        + "- **Build:** #" + build_num + "\\n"
        + "- **Pipeline/job:** " + (build_url or "See CircleCI project") + "\\n\\n"
        + "\\n\\n".join(sections)
        + "\\n\\n_Logs are redacted and truncated. Review CircleCI for full context._"
    )[:MAX_BODY_CHARS]
    token = os.getenv("KDO_CI_REPORT_TOKEN")
    if not token:
        print("KDO_CI_REPORT_TOKEN is not configured; logs remain available as CircleCI artifacts.")
        return 0
    endpoint = "https://api.github.com/repos/" + owner + "/" + repo + "/issues"
    payload = json.dumps({"title": "CI failure: " + branch + " build #" + build_num, "body": body}).encode()
    request = urllib.request.Request(
        endpoint, data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "KDO-CircleCI-Failure-Reporter",
        }, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode())
            print("Created GitHub failure report: " + result.get("html_url", "issue created"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print("GitHub failure reporting unsuccessful: " + type(exc).__name__)
    return 0

if __name__ == "__main__":
    sys.exit(main())
