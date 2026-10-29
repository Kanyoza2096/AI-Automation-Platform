from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from scripts.staging_e2e_publish import Config, load_config, run


def test_staging_e2e_requires_explicit_live_flag():
    cfg = Config("https://staging.example", "token", "ws", "brand")
    report = run(cfg, live=False, prefix="test", wait_seconds=0)
    assert report["status"] == "blocked"


def test_staging_e2e_requires_workspace_and_brand(monkeypatch):
    for key in (
        "KANYOZA_STAGING_BASE_URL",
        "KANYOZA_STAGING_TOKEN",
        "KANYOZA_STAGING_WORKSPACE_ID",
        "KANYOZA_STAGING_BRAND_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KANYOZA_STAGING_BASE_URL", "https://staging.example")
    monkeypatch.setenv("KANYOZA_STAGING_TOKEN", "token")
    with pytest.raises(SystemExit, match="workspace_id, brand_id"):
        load_config()

@pytest.mark.asyncio
async def test_trend_bridge_fails_closed_without_workspace():
    from services.trend_publish_bridge import handle_trend_publish_requested
    result = await handle_trend_publish_requested({"brand_id": "brand", "content": "must not publish"})
    assert result["ok"] is False
    assert result["error"] == "workspace_required"

@pytest.mark.asyncio
async def test_publish_worker_fails_closed_when_job_workspace_missing():
    from services.publish_worker import publish_job_task
    from unittest.mock import MagicMock

    job = MagicMock(workspace_id=None, brand_id="brand", decision_id="decision", topic="x", platform="facebook", result={"caption": "x"})
    job_service = MagicMock()
    job_service.find_by_id = lambda job_id: __import__("asyncio").sleep(0, result=job)
    container = MagicMock()
    container.try_resolve.return_value = job_service
    result = await publish_job_task("job-1", container=container)
    assert result["ok"] is False
    assert result["error"] == "workspace_required"
