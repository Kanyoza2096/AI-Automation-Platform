from __future__ import annotations
import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.asyncio
async def test_orchestration_delegates_to_canonical():
    from services.publish_orchestration import run_publish
    with patch("domain.publishing.canonical_pipeline.run_canonical_publish") as inner:
        async def _fake(**kw):
            return {"ok": True, "dry_run": True, "delegated": True}
        inner.side_effect = _fake
        result = await run_publish(workspace_id="ws", dry_run=True)
    assert result.get("delegated") is True


@pytest.mark.asyncio
async def test_pipeline_stage_order():
    from domain.publishing.canonical_pipeline import run_canonical_publish
    with patch("core.publish_authorization.assert_publish_allowed") as gate:
        decision = MagicMock(); decision.decision_id = "dec-order"
        gate.return_value = decision
        result = await run_canonical_publish(workspace_id="ws", dry_run=True)
    stages = [s["stage"] for s in result["trace"]]
    assert stages.index("content") < stages.index("guardian")
    assert stages.index("guardian") < stages.index("metabolism")
    assert stages.index("metabolism") < stages.index("council")
    assert stages.index("council") < stages.index("director")
