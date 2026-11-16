from services.director.engineering_awareness import (
    _correlate_deployments,
    _deployment_commit,
    _deployment_status,
    _service_repository,
)


def test_render_deployment_commit_accepts_structured_commit():
    assert _deployment_commit({"commit": {"id": "ABC123"}}) == "abc123"


def test_render_deployment_status_is_normalized():
    assert _deployment_status({"status": "live"}) == "live"
    assert _deployment_status({"status": "build_in_progress"}) == "in_progress"
    assert _deployment_status({"status": "build_failed"}) == "failed"
    assert _deployment_status({"status": "unknown-provider-state"}) == "unknown-provider-state"


def test_render_repository_url_is_normalized():
    assert _service_repository({"repo": "https://github.com/Kanyoza2096/AI-Automation-Platform.git"}) == "https://github.com/kanyoza2096/ai-automation-platform"


def test_deployment_requires_exact_commit_match_and_live_status():
    repositories = [{
        "repository": "Kanyoza2096/AI-Automation-Platform",
        "merged_pull_requests": [{
            "merge_commit_sha": "merge-sha",
            "head_sha": "head-sha",
            "operational_verification_state": "unverified",
        }],
    }]
    deployments = [{
        "repository": "Kanyoza2096/AI-Automation-Platform",
        "commit_sha": "merge-sha",
        "status": "live",
        "deploy_id": "dep-1",
    }]
    _correlate_deployments(repositories, deployments)
    pull = repositories[0]["merged_pull_requests"][0]
    assert pull["deployment_state"] == "deployed"
    assert [item["deploy_id"] for item in pull["deployment_evidence"]] == ["dep-1"]
    assert pull["operational_verification_state"] == "unverified"


def test_deployment_from_another_commit_is_not_attributed():
    repositories = [{
        "repository": "Kanyoza2096/AI-Automation-Platform",
        "merged_pull_requests": [{
            "merge_commit_sha": "merge-sha",
            "head_sha": "head-sha",
        }],
    }]
    deployments = [{
        "repository": "Kanyoza2096/AI-Automation-Platform",
        "commit_sha": "later-unrelated-sha",
        "status": "live",
        "deploy_id": "dep-2",
    }]
    _correlate_deployments(repositories, deployments)
    pull = repositories[0]["merged_pull_requests"][0]
    assert pull["deployment_state"] == "unverified"
    assert pull["deployment_evidence"] == []


def test_ledger_records_merge_deployment_workflow_and_migration_evidence_separately():
    from services.director.engineering_ledger import events_from_snapshot

    snapshot = {
        "repositories": [{
            "repository": "org/repo",
            "merged_pull_requests": [{
                "number": 42,
                "title": "Add migration",
                "merged_at": "2026-11-16T10:00:00Z",
                "merge_commit_sha": "abc123",
                "changed_paths": ["migrations/171_example.sql", "services/director/core.py"],
                "url": "https://github.com/org/repo/pull/42",
            }],
            "latest_workflow": {
                "id": 99, "status": "completed", "conclusion": "success",
                "head_sha": "abc123", "updated_at": "2026-11-16T10:01:00Z",
            },
        }],
        "render": {"deployments": [{
            "deploy_id": "dep-1", "repository": "org/repo",
            "commit_sha": "abc123", "status": "live",
        }]},
    }
    events = events_from_snapshot(snapshot)
    by_type = {event["event_type"]: event for event in events}
    assert set(by_type) == {
        "pull_request_merged", "database_migration_merged", "workflow_run", "deployment",
    }
    assert by_type["database_migration_merged"]["payload"]["migration_state"] == "merged_not_applied"
    assert by_type["workflow_run"]["payload"]["correlated_to_merge"] is False
    assert by_type["deployment"]["commit_sha"] == "abc123"


def test_ledger_event_keys_are_deterministic_for_idempotent_snapshots():
    from services.director.engineering_ledger import events_from_snapshot

    snapshot = {"repositories": [{
        "repository": "org/repo",
        "merged_pull_requests": [{"number": 42, "merged_at": "2026-11-16T10:00:00Z"}],
    }], "render": {"deployments": []}}
    first = events_from_snapshot(snapshot)
    second = events_from_snapshot(snapshot)
    assert [event["event_key"] for event in first] == [event["event_key"] for event in second]
