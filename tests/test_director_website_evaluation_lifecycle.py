import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

CLAIM_TOKEN = "11111111-1111-4111-8111-111111111111"

from services.director.website_event_adapter import DirectorWebsiteEventAdapter


def _snapshot():
    context = {"schema_version": "1.0", "workspace_id": "ws-1", "website": {"name": "GrowthBridge"}}
    encoded = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {
        "workspace_id": "ws-1",
        "integration_id": "site-1",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": hashlib.sha256(encoded).hexdigest(),
        "last_event_id": "evt-1",
        "trust_class": "untrusted_business_context",
        "instructions_are_data": True,
        "context": context,
    }


class _Table:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.filters = {}

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, _count):
        return self

    def execute(self):
        rows = self.db.rows.get(self.name, [])
        return SimpleNamespace(data=[
            row for row in rows
            if all(row.get(key) == value for key, value in self.filters.items())
        ])


class _RPC:
    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        p = self.params
        if self.name == "claim_director_website_evaluation":
            row = self.db.evaluations.get(p["p_evaluation_id"])
            scope = (p["p_workspace_id"], p["p_integration_id"], p["p_event_id"], p["p_context_hash"])
            if row:
                actual = (row["workspace_id"], row["integration_id"], row["event_id"], row["context_hash"])
                if actual != scope:
                    raise RuntimeError("director_website_evaluation_scope_mismatch")
                if row["status"] == "evaluated":
                    return SimpleNamespace(data=[{
                        "claimed": False, "evaluation_status": "evaluated",
                        "evaluation_payload": row["payload"],
                    }])
                if row["status"] == "pending":
                    return SimpleNamespace(data=[{
                        "claimed": False, "evaluation_status": "pending",
                        "evaluation_payload": None,
                    }])
                row["status"] = "pending"
            else:
                row = {
                    "workspace_id": p["p_workspace_id"],
                    "integration_id": p["p_integration_id"],
                    "event_id": p["p_event_id"],
                    "context_hash": p["p_context_hash"],
                    "status": "pending",
                    "payload": None,
                    "lease_token": CLAIM_TOKEN,
                }
                self.db.evaluations[p["p_evaluation_id"]] = row
            return SimpleNamespace(data=[{
                "claimed": True, "evaluation_status": "pending",
                "evaluation_payload": None, "claim_token": CLAIM_TOKEN,
            }])
        if self.name == "complete_director_website_evaluation":
            row = self.db.evaluations[p["p_evaluation_id"]]
            payload = p["p_payload"]
            assert p["p_claim_token"] == CLAIM_TOKEN
            assert payload["workspace_id"] == row["workspace_id"]
            assert payload["integration_id"] == row["integration_id"]
            assert payload["event_id"] == row["event_id"]
            assert payload["context_hash"] == row["context_hash"]
            assert payload["advisory_only"] is True
            assert payload["authority"] == "none"
            assert payload["tasks_created"] is False
            assert payload["public_publishing_authorized"] is False
            assert payload["deployment_authorized"] is False
            row.update(status="evaluated", payload=payload, lease_token=None)
            return SimpleNamespace(data=True)
        if self.name == "fail_director_website_evaluation":
            row = self.db.evaluations[p["p_evaluation_id"]]
            assert p["p_claim_token"] == CLAIM_TOKEN
            row.update(status="failed", payload=None, lease_token=None)
            return SimpleNamespace(data=True)
        raise AssertionError(self.name)


class _DB:
    def __init__(self):
        self.rows = {"director_website_context_snapshots": [_snapshot()]}
        self.evaluations = {}

    def table(self, name):
        return _Table(self, name)

    def rpc(self, name, params):
        return _RPC(self, name, params)


class _Director:
    def register_observer(self, _observer):
        pass


class _Bus:
    pass


def _evaluation_result(snapshot):
    identity = hashlib.sha256(
        f"ws-1:site-1:evt-1:{snapshot['content_hash']}".encode()
    ).hexdigest()[:24]
    return {
        "schema_version": "kdo.director.website-evaluation.v1",
        "evaluation_id": f"website_eval_{identity}",
        "workspace_id": "ws-1",
        "integration_id": "site-1",
        "event_id": "evt-1",
        "context_hash": snapshot["content_hash"],
        "significance": "medium",
        "urgency": "normal",
        "action_warranted": True,
        "business_implications": ["A review may be useful."],
        "evidence_refs": [f"website_context:{snapshot['content_hash']}"],
        "recommended_objective": {
            "title": "Review website positioning",
            "description": "Assess whether positioning reflects current priorities.",
            "success_criteria": ["A verified review is recorded."],
            "constraints": ["Do not publish or deploy."],
        },
        "status": "evaluated",
        "record_scope": "workspace",
        "authority": "none",
        "advisory_only": True,
        "tasks_created": False,
        "content_generation_started": False,
        "public_publishing_authorized": False,
        "deployment_authorized": False,
    }


def _adapter(db, evaluator):
    return DirectorWebsiteEventAdapter(_Director(), _Bus(), db, website_evaluator=evaluator)


def test_verified_snapshot_is_evaluated_and_persisted_as_advisory_workspace_decision():
    db = _DB()
    calls = []
    evaluator = lambda **kwargs: calls.append(kwargs) or _evaluation_result(kwargs["snapshot"])
    result = _adapter(db, evaluator)._evaluate_context_snapshot("ws-1", "site-1", "evt-1")

    assert result["status"] == "evaluated"
    assert result["evaluation"]["workspace_id"] == "ws-1"
    assert result["evaluation"]["planning_status"] == "awaiting_workspace_scoped_governed_planner"
    assert result["evaluation"]["approval_required"] is True
    assert result["evaluation"]["plan_created"] is False
    assert len(calls) == 1
    assert len(db.evaluations) == 1
    persisted = next(iter(db.evaluations.values()))
    assert persisted["status"] == "evaluated"
    assert persisted["payload"]["tasks_created"] is False


def test_evaluation_cannot_claim_content_generation_started():
    db = _DB()

    def unsafe_evaluator(**kwargs):
        result = _evaluation_result(kwargs["snapshot"])
        result["content_generation_started"] = True
        return result

    with pytest.raises(RuntimeError, match="scope_or_authority_invalid"):
        _adapter(db, unsafe_evaluator)._evaluate_context_snapshot("ws-1", "site-1", "evt-1")

    persisted = next(iter(db.evaluations.values()))
    assert persisted["status"] == "failed"
    assert persisted["payload"] is None


def test_completed_evaluation_is_reused_without_another_model_call():
    db = _DB()
    calls = []
    evaluator = lambda **kwargs: calls.append(kwargs) or _evaluation_result(kwargs["snapshot"])
    adapter = _adapter(db, evaluator)

    first = adapter._evaluate_context_snapshot("ws-1", "site-1", "evt-1")
    second = adapter._evaluate_context_snapshot("ws-1", "site-1", "evt-1")

    assert first["status"] == second["status"] == "evaluated"
    assert len(calls) == 1
    assert second["evaluation"]["evaluation_id"] == first["evaluation"]["evaluation_id"]


def test_active_evaluation_claim_prevents_duplicate_model_call():
    db = _DB()
    calls = []
    evaluator = lambda **kwargs: calls.append(kwargs) or _evaluation_result(kwargs["snapshot"])
    adapter = _adapter(db, evaluator)
    snapshot = db.rows["director_website_context_snapshots"][0]
    evaluation = _evaluation_result(snapshot)
    evaluation_id = evaluation["evaluation_id"]
    db.evaluations[evaluation_id] = {
        "workspace_id": "ws-1", "integration_id": "site-1",
        "event_id": "evt-1", "context_hash": snapshot["content_hash"],
        "status": "pending", "payload": None,
    }

    result = adapter._evaluate_context_snapshot("ws-1", "site-1", "evt-1")

    assert result == {"status": "pending", "evaluation": None}
    assert calls == []


def test_failed_evaluation_claim_is_retried_and_then_persisted():
    db = _DB()
    snapshot = db.rows["director_website_context_snapshots"][0]
    expected = _evaluation_result(snapshot)
    db.evaluations[expected["evaluation_id"]] = {
        "workspace_id": "ws-1", "integration_id": "site-1",
        "event_id": "evt-1", "context_hash": snapshot["content_hash"],
        "status": "failed", "payload": None, "lease_token": None,
    }
    calls = []
    evaluator = lambda **kwargs: calls.append(kwargs) or expected

    result = _adapter(db, evaluator)._evaluate_context_snapshot("ws-1", "site-1", "evt-1")

    assert result["status"] == "evaluated"
    assert len(calls) == 1
    assert db.evaluations[expected["evaluation_id"]]["status"] == "evaluated"


def test_evaluator_is_not_called_for_snapshot_with_wrong_event_identity():
    db = _DB()
    calls = []
    evaluator = lambda **kwargs: calls.append(kwargs) or _evaluation_result(kwargs["snapshot"])

    with pytest.raises(RuntimeError, match="snapshot_scope_mismatch"):
        _adapter(db, evaluator)._evaluate_context_snapshot("ws-1", "site-1", "evt-other")

    assert calls == []
    assert db.evaluations == {}


def test_workspace_mismatch_does_not_read_another_workspace_snapshot():
    db = _DB()
    calls = []
    evaluator = lambda **kwargs: calls.append(kwargs) or _evaluation_result(kwargs["snapshot"])

    with pytest.raises(RuntimeError, match="snapshot_unavailable"):
        _adapter(db, evaluator)._evaluate_context_snapshot("ws-other", "site-1", "evt-1")

    assert calls == []
    assert db.evaluations == {}
