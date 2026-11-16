from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from services.director.website_evaluation import WebsiteEvaluationError, make_llm_website_context_evaluator


def snapshot():
    context = {"schema_version": "1.0", "workspace_id": "ws-1", "website": {"name": "GrowthBridge"}}
    encoded = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {
        "workspace_id": "ws-1", "integration_id": "site-1",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": hashlib.sha256(encoded).hexdigest(),
        "last_event_id": "evt-12345678", "trust_class": "untrusted_business_context",
        "instructions_are_data": True, "context": context,
    }


def evaluation_data():
    snap = snapshot()
    return {
        "significance": "medium", "urgency": "normal", "action_warranted": True,
        "business_implications": ["The services catalogue may need review."],
        "evidence_refs": [f"website_context:{snap['content_hash']}", "website_event:evt-12345678"],
        "recommended_objective": {
            "title": "Review the services catalogue",
            "description": "Assess whether published services match current priorities.",
            "success_criteria": ["A verified assessment is recorded."],
            "constraints": ["Do not publish or modify website content."],
        },
    }


class Gateway:
    def __init__(self, data=None, ok=True, reason=""):
        self.data, self.ok, self.reason, self.calls = data or evaluation_data(), ok, reason, []

    def complete_structured(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return SimpleNamespace(ok=self.ok, data=self.data, reason=self.reason)


def test_workspace_scoped_advisory_evaluation():
    gateway = Gateway()
    result = make_llm_website_context_evaluator(gateway)(
        workspace_id="ws-1", integration_id="site-1", snapshot=snapshot()
    )
    assert result["schema_version"] == "kdo.director.website-evaluation.v1"
    assert result["record_scope"] == "workspace"
    assert result["authority"] == "none" and result["advisory_only"] is True
    assert result["tasks_created"] is False
    assert result["content_generation_started"] is False
    assert result["public_publishing_authorized"] is False
    assert result["deployment_authorized"] is False
    assert gateway.calls[0][1]["workspace_id"] == "ws-1"
    assert gateway.calls[0][1]["caller"] == "director"
    assert "untrusted business data" in gateway.calls[0][0].lower()


@pytest.mark.parametrize("workspace,integration,error", [
    ("ws-other", "site-1", "snapshot_workspace_mismatch"),
    ("ws-1", "site-other", "snapshot_integration_mismatch"),
])
def test_rejects_cross_scope_snapshot_before_llm(workspace, integration, error):
    gateway = Gateway()
    with pytest.raises(WebsiteEvaluationError, match=error):
        make_llm_website_context_evaluator(gateway)(
            workspace_id=workspace, integration_id=integration, snapshot=snapshot()
        )
    assert gateway.calls == []


def test_rejects_tampered_context_hash_before_llm():
    snap = snapshot()
    snap["context"]["website"]["name"] = "tampered"
    gateway = Gateway()
    with pytest.raises(WebsiteEvaluationError, match="snapshot_content_hash_mismatch"):
        make_llm_website_context_evaluator(gateway)(
            workspace_id="ws-1", integration_id="site-1", snapshot=snap
        )
    assert gateway.calls == []


def test_rejects_instruction_boundary_violation_before_llm():
    snap = snapshot()
    snap["instructions_are_data"] = False
    gateway = Gateway()
    with pytest.raises(WebsiteEvaluationError, match="snapshot_instruction_boundary_invalid"):
        make_llm_website_context_evaluator(gateway)(
            workspace_id="ws-1", integration_id="site-1", snapshot=snap
        )
    assert gateway.calls == []


def test_rejects_context_workspace_mismatch_even_with_recomputed_hash():
    snap = snapshot()
    snap["context"]["workspace_id"] = "ws-other"
    encoded = json.dumps(snap["context"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    snap["content_hash"] = hashlib.sha256(encoded).hexdigest()
    gateway = Gateway()
    with pytest.raises(WebsiteEvaluationError, match="website_context_workspace_mismatch"):
        make_llm_website_context_evaluator(gateway)(
            workspace_id="ws-1", integration_id="site-1", snapshot=snap
        )
    assert gateway.calls == []


def test_rejects_invented_evidence_references():
    data = evaluation_data()
    data["evidence_refs"] = ["website_event:invented"]
    gateway = Gateway(data)
    with pytest.raises(WebsiteEvaluationError, match="website_evaluation_evidence_reference_invalid"):
        make_llm_website_context_evaluator(gateway)(
            workspace_id="ws-1", integration_id="site-1", snapshot=snapshot()
        )


def test_requires_objective_when_action_warranted_and_forbids_otherwise():
    data = evaluation_data()
    data["recommended_objective"] = None
    with pytest.raises(WebsiteEvaluationError, match="action_warranted_requires_objective"):
        make_llm_website_context_evaluator(Gateway(data))(
            workspace_id="ws-1", integration_id="site-1", snapshot=snapshot()
        )
    data = evaluation_data()
    data["action_warranted"] = False
    with pytest.raises(WebsiteEvaluationError, match="objective_not_allowed_without_warranted_action"):
        make_llm_website_context_evaluator(Gateway(data))(
            workspace_id="ws-1", integration_id="site-1", snapshot=snapshot()
        )


def test_gateway_failure_is_not_fabricated_as_an_evaluation():
    with pytest.raises(WebsiteEvaluationError, match="website_evaluation_unavailable:budget_exhausted"):
        make_llm_website_context_evaluator(Gateway(ok=False, reason="budget_exhausted"))(
            workspace_id="ws-1", integration_id="site-1", snapshot=snapshot()
        )


def test_rejects_stale_snapshot_before_llm():
    snap = snapshot()
    snap["fetched_at"] = "2020-01-01T00:00:00+00:00"
    gateway = Gateway()
    with pytest.raises(WebsiteEvaluationError, match="snapshot_stale"):
        make_llm_website_context_evaluator(gateway)(
            workspace_id="ws-1", integration_id="site-1", snapshot=snap
        )
    assert gateway.calls == []
