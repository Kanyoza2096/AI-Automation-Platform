from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_publish_step_creates_bounded_decision_for_scheduled_trigger():
    from workflows.post_workflow import PublishStep

    brand = MagicMock(id="brand-1", workspace_id="ws-1", name="Demo")
    step = PublishStep(registry=MagicMock(), brand=brand)

    with patch(
        "core.publish_authorization.issue_internal_publish_decision"
    ) as issue, patch(
        "services.publish_orchestration.execute_governed_publish", new_callable=AsyncMock
    ) as publish:
        issue.return_value = MagicMock(decision_id="dec-auto-1")
        publish.return_value = {"status": "succeeded", "output": {"ok": True, "queued": True, "outbox_id": "obx-1"}}

        result = await step.async_run({
            "caption": "hello",
            "topic": "topic",
            "platform": "facebook",
            "source": "scheduled",
            "risk_level": "low",
        })

    issue.assert_called_once()
    assert issue.call_args.kwargs["workspace_id"] == "ws-1"
    assert issue.call_args.kwargs["source"] == "scheduled"
    publish.assert_called_once()
    assert publish.call_args.kwargs["decision_id"] == "dec-auto-1"
    assert publish.call_args.kwargs["brand_id"] == "brand-1"
    assert result["queued"] is True


def test_internal_publish_decision_is_bounded_to_low_risk_and_trusted_triggers():
    from core.publish_authorization import issue_internal_publish_decision
    from core.decision_gate import DecisionGateError, decision_gate

    d = issue_internal_publish_decision(
        workspace_id="ws-safe",
        source="manual",
        risk_level="low",
        brand_id="brand-1",
        platform="facebook",
    )
    assert d.approved is True
    assert d.workspace_id == "ws-safe"
    assert d.metadata["trigger_source"] == "manual"

    with pytest.raises(DecisionGateError):
        issue_internal_publish_decision(
            workspace_id="ws-safe", source="manual", risk_level="high"
        )
    with pytest.raises(DecisionGateError):
        issue_internal_publish_decision(
            workspace_id="ws-safe", source="unknown", risk_level="low"
        )


@pytest.mark.asyncio
async def test_connector_receives_brand_for_tenant_specific_facebook_credentials():
    from domain.publishing.canonical_pipeline import _connector_execute

    plugin = MagicMock()
    plugin.publish.return_value = {"ok": True, "post_id": "fb-123"}
    container = MagicMock()
    container.social_plugins = {"facebook": plugin}
    container.industry_plugins = {}
    brand = MagicMock(id="brand-7", workspace_id="ws-7", name="Tenant Page")

    with patch("repositories.social_account_repository.SocialAccountRepository.find_by_brand_and_platform", return_value=None):
        result = await _connector_execute(
            container=container,
        workspace_id="ws-7",
        brand_id="brand-7",
        brand=brand,
        platform="facebook",
        caption="hello",
        topic="topic",
        decision_id="dec-7",
        idempotency_key=None,
            allow_simulate=False,
        )

    assert result["status"] == "ok"
    assert result["platform_post_id"] == "fb-123"
    assert plugin.publish.call_args.kwargs["brand"] is brand


@pytest.mark.asyncio
async def test_canonical_pipeline_executes_facebook_plugin_end_to_end_after_governance():
    from domain.publishing.canonical_pipeline import run_canonical_publish
    from core.decision_gate import decision_gate, DecisionSource

    plugin = MagicMock()
    plugin.publish.return_value = {"ok": True, "post_id": "fb-e2e-1"}
    container = MagicMock()
    container.social_plugins = {"facebook": plugin}
    container.industry_plugins = {}
    brand = MagicMock(id="brand-e2e", workspace_id="ws-e2e", name="E2E")
    decision = decision_gate.issue(
        source=DecisionSource.DIRECTOR_AUTO, workspace_id="ws-e2e",
        action_name="publish", approved=True, risk_level="low",
    )

    class FakeLedger:
        def __init__(self, db=None): self.record = MagicMock(status="started", execution_id="exe-e2e", locked_by=None, provider_object_id=None)
        def begin(self, **kwargs): return self.record
        def claim(self, record): return record
        def complete(self, record, **kwargs):
            self.record.status = kwargs["status"]
            self.record.provider_object_id = kwargs.get("provider_object_id")
            return self.record

    with patch("services.execution_ledger.ExecutionLedger", FakeLedger), \
         patch("domain.publishing.canonical_pipeline._guardian_evaluate", AsyncMock(return_value={"status": "allow"})), \
         patch("domain.publishing.canonical_pipeline._metabolism_check", AsyncMock(return_value={"status": "ok"})), \
         patch("domain.publishing.canonical_pipeline._council_deliberate", AsyncMock(return_value={"status": "skipped"})): 
        with patch("repositories.social_account_repository.SocialAccountRepository.find_by_brand_and_platform", return_value=None):
            result = await run_canonical_publish(
                container=container, workspace_id="ws-e2e", brand_id="brand-e2e",
                brand=brand, platform="facebook", topic="hello", caption="hello world",
                decision_id=decision.decision_id, durable=False,
            )

    assert result["ok"] is True
    assert result["platform_post_id"] == "fb-e2e-1"
    assert plugin.publish.call_args.kwargs["brand"] is brand

@pytest.mark.asyncio
async def test_director_automated_content_crosses_action_kernel_before_publish():
    from services.director_bindings import _build_handlers_for

    director = MagicMock()
    container = MagicMock()
    handlers = _build_handlers_for(director, container)
    publish = handlers["publish_scheduled_content"]

    governed = {"status": "approval_required", "intent_id": "act-auto-1", "accepted": False}
    with patch(
        "services.publish_orchestration.execute_governed_publish",
        new_callable=AsyncMock,
        return_value=governed,
    ) as execute, patch(
        "services.facebook.facebook.post_to_page"
    ) as raw_facebook:
        result = await publish({
            "workspace_id": "ws-auto",
            "brand_id": "brand-auto",
            "source": "trend_intelligence",
            "job_id": "trend-job-1",
            "topic": "AI",
            "content": "A governed automated post",
            "decision_id": "dec-auto-1",
        })

    assert result["ok"] is True
    assert result["service"] == "action_kernel"
    assert result["governed"] is True
    execute.assert_awaited_once()
    assert execute.call_args.kwargs["workspace_id"] == "ws-auto"
    assert execute.call_args.kwargs["brand_id"] == "brand-auto"
    assert execute.call_args.kwargs["decision_id"] == "dec-auto-1"
    assert execute.call_args.kwargs["idempotency_key"] == "trend-job-1"
    raw_facebook.assert_not_called()


@pytest.mark.asyncio
async def test_director_automated_content_fails_closed_without_workspace():
    from services.director_bindings import _build_handlers_for

    handlers = _build_handlers_for(MagicMock(), MagicMock())
    publish = handlers["publish_scheduled_content"]
    result = await publish({
        "brand_id": "brand-auto",
        "source": "trend_intelligence",
        "job_id": "trend-job-2",
        "content": "must not publish",
    })
    assert result["ok"] is False
    assert "workspace_id" in result.get("reason", "")
