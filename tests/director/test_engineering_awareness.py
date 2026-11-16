from datetime import datetime, timezone

import pytest

from services.director.engineering_awareness import build_engineering_awareness


class FakeGitHub:
    github_token = "configured"

    async def _github_request(self, path):
        if "/pulls?" in path:
            return [
                {
                    "number": 12,
                    "title": "Add Director engineering awareness",
                    "body": "Read-only engineering inventory.",
                    "merged_at": "2026-11-16T18:00:00Z",
                    "merge_commit_sha": "merge-sha-12",
                    "head": {"sha": "head-sha-12"},
                    "base": {"ref": "staging"},
                    "changed_files": 3,
                    "additions": 90,
                    "deletions": 2,
                    "html_url": "https://github.com/Kanyoza2096/AI-Automation-Platform/pull/12",
                },
                {
                    "number": 11,
                    "title": "Old change",
                    "merged_at": "2026-09-01T10:00:00Z",
                },
                {
                    "number": 13,
                    "title": "Closed but not merged",
                    "merged_at": None,
                },
            ]
        if "/actions/runs?" in path:
            return {"workflow_runs": [{
                "name": "Build",
                "status": "completed",
                "conclusion": "success",
                "head_sha": "unrelated-sha",
                "updated_at": "2026-11-16T18:10:00Z",
                "html_url": "https://github.com/example/run/1",
            }]}
        raise AssertionError(f"Unexpected GitHub request: {path}")


class MissingTokenGitHub:
    github_token = ""


@pytest.mark.asyncio
async def test_awareness_reports_merged_separately_from_deployment_and_runtime():
    snapshot = await build_engineering_awareness(
        plugin=FakeGitHub(),
        now=datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc),
    )

    assert snapshot["status"] == "available"
    assert snapshot["summary"]["repositories_available"] == 3
    assert snapshot["summary"]["merged_changes"] == 3
    repo = snapshot["repositories"][0]
    assert repo["merged_pull_requests"][0]["number"] == 12
    assert repo["merged_pull_requests"][0]["implementation_state"] == "merged"
    assert repo["merged_pull_requests"][0]["deployment_state"] == "unverified"
    assert repo["merged_pull_requests"][0]["operational_verification_state"] == "unverified"
    assert repo["latest_workflow"]["conclusion"] == "success"
    assert repo["latest_workflow"]["correlated_to_merge"] is False
    assert snapshot["authority"]["read_only"] is True
    assert snapshot["authority"]["execution_authorized"] is False


@pytest.mark.asyncio
async def test_awareness_fails_honestly_when_github_token_is_missing():
    snapshot = await build_engineering_awareness(
        plugin=MissingTokenGitHub(),
        now=datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc),
    )

    assert snapshot["status"] == "unavailable"
    assert snapshot["source"] == "none"
    assert snapshot["repositories"] == []
    assert "not configured" in snapshot["message"]
    assert snapshot["authority"]["plans_created"] is False
