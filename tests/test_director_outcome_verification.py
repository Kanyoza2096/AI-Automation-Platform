from datetime import datetime, timezone

from services.director.core import _verify_schedule_change, _verify_worker_scale


def snapshot(metrics, *, status="healthy", age_seconds=0):
    observed_at = datetime.now(timezone.utc).timestamp() - age_seconds
    return {"authoritative_domain_observer": {
        "status": status,
        "observed_at": datetime.fromtimestamp(observed_at, timezone.utc).isoformat(),
        "metrics": metrics,
    }}


def test_schedule_verification_requires_observed_matching_hours_and_workspace():
    params = {"brand_id": "brand-1", "workspace_id": "ws-1", "posting_hours": [17, 9]}
    observed = snapshot({"brands": {"brand-1": {
        "workspace_id": "ws-1", "posting_schedule": {"content_hours": [9, 17]},
    }}})
    assert _verify_schedule_change(params, {}, observed) is True


def test_schedule_verification_rejects_missing_or_mismatched_evidence():
    params = {"brand_id": "brand-1", "workspace_id": "ws-1", "posting_hours": [9, 17]}
    assert _verify_schedule_change(params, {}, {}) is None
    stale = snapshot({"brands": {"brand-1": {
        "workspace_id": "ws-1", "posting_schedule": {"content_hours": [9, 17]},
    }}}, age_seconds=301)
    assert _verify_schedule_change(params, {}, stale) is None
    unknown_health = snapshot({"brands": {"brand-1": {
        "workspace_id": "ws-1", "posting_schedule": {"content_hours": [9, 17]},
    }}}, status="unknown")
    assert _verify_schedule_change(params, {}, unknown_health) is None
    wrong_hours = snapshot({"brands": {"brand-1": {
        "workspace_id": "ws-1", "posting_schedule": {"content_hours": [8, 18]},
    }}})
    assert _verify_schedule_change(params, {}, wrong_hours) is False
    wrong_workspace = snapshot({"brands": {"brand-1": {
        "workspace_id": "ws-2", "posting_schedule": {"content_hours": [9, 17]},
    }}})
    assert _verify_schedule_change(params, {}, wrong_workspace) is False


def test_worker_scale_verification_requires_exact_observed_count():
    params = {"pool_name": "content", "workspace_id": "ws-1", "target_workers": 4}
    observed = snapshot({"worker_pools": {"content": {
        "workspace_id": "ws-1", "worker_count": 4,
    }}})
    assert _verify_worker_scale(params, {}, observed) is True
    wrong_count = snapshot({"worker_pools": {"content": {
        "workspace_id": "ws-1", "worker_count": 3,
    }}})
    assert _verify_worker_scale(params, {}, wrong_count) is False


def test_worker_scale_rejects_intent_only_missing_and_cross_workspace_data():
    params = {"pool_name": "content", "workspace_id": "ws-1", "target_workers": 4}
    assert _verify_worker_scale(params, {}, {}) is None
    intent_only = snapshot({"worker_scale_intents": {"content": {"target_workers": 4}}})
    assert _verify_worker_scale(params, {}, intent_only) is None
    wrong_workspace = snapshot({"worker_pools": {"content": {
        "workspace_id": "ws-2", "worker_count": 4,
    }}})
    assert _verify_worker_scale(params, {}, wrong_workspace) is False


def test_worker_scale_rejects_boolean_and_invalid_counts():
    params = {"pool_name": "content", "workspace_id": "ws-1", "target_workers": True}
    observed = snapshot({"worker_pools": {"content": {
        "workspace_id": "ws-1", "worker_count": 1,
    }}})
    assert _verify_worker_scale(params, {}, observed) is False
    params["target_workers"] = 101
    assert _verify_worker_scale(params, {}, observed) is False
