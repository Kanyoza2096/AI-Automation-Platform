"""Failure matrix — Phase D gaps closed."""
from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_decision_gate_denial_is_deterministic():
    from domain.publishing.canonical_pipeline import run_canonical_publish
    from core.decision_gate import DecisionGateError

    with patch("core.publish_authorization.assert_publish_allowed",
               side_effect=DecisionGateError("denied")):
        result = await run_canonical_publish(
            workspace_id="ws-1", topic="t", dry_run=True, durable=False,
        )
    assert result["ok"] is False
    assert result["code"] == "decision_denied"


@pytest.mark.asyncio
async def test_dry_run_never_hits_outbox():
    from domain.publishing.canonical_pipeline import run_canonical_publish

    with patch("core.publish_authorization.assert_publish_allowed") as gate:
        decision = MagicMock(); decision.decision_id = "dec-dry-1"
        gate.return_value = decision
        result = await run_canonical_publish(
            workspace_id="ws-1", topic="hello", dry_run=True, durable=True,
        )
    assert result["ok"] is True
    assert result.get("dry_run") is True
    assert "outbox_id" not in result
    stages = [s["stage"] for s in result["trace"]]
    assert "outbox" not in stages


@pytest.mark.asyncio
async def test_empty_workspace_rejected():
    from domain.publishing.canonical_pipeline import run_canonical_publish
    result = await run_canonical_publish(workspace_id="", dry_run=True)
    assert result["ok"] is False
    assert result["code"] == "workspace_required"


@pytest.mark.asyncio
async def test_outbox_failure_does_not_simulate_success():
    from domain.publishing.canonical_pipeline import run_canonical_publish

    with patch("core.publish_authorization.assert_publish_allowed") as gate:
        decision = MagicMock(); decision.decision_id = "dec-obx"
        gate.return_value = decision
        with patch("domain.publishing.canonical_pipeline._guardian_evaluate", new=AsyncMock(return_value={"status": "allow"})), \
             patch("domain.publishing.canonical_pipeline._metabolism_check", new=AsyncMock(return_value={"status": "ok"})), \
             patch("domain.publishing.canonical_pipeline._council_deliberate", new=AsyncMock(return_value={"status": "skipped"})), \
             patch("core.publish_outbox.publish_outbox") as obx:
            obx.enqueue.side_effect = RuntimeError("db down")
            result = await run_canonical_publish(
                workspace_id="ws-1", dry_run=False, durable=True,
            )
    assert result["ok"] is False
    assert result["code"] == "outbox_enqueue_failed"
    assert "sim_" not in str(result)


@pytest.mark.asyncio
async def test_trace_typed_fields_present():
    from domain.publishing.canonical_pipeline import run_canonical_publish

    with patch("core.publish_authorization.assert_publish_allowed") as gate:
        decision = MagicMock(); decision.decision_id = "dec-2"
        gate.return_value = decision
        result = await run_canonical_publish(workspace_id="ws-1", dry_run=True)
    assert result["ok"]
    assert "correlation_id" in result
    stage = result["trace"][0]
    assert "stage" in stage and "status" in stage
    assert "started_at" in stage and "completed_at" in stage
