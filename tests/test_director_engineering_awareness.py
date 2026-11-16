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
