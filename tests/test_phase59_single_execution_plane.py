import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def test_kernel_publishing_adapter_marks_execution_as_kernel_managed():
    source = (ROOT / "domain/publishing/action_handler.py").read_text(encoding="utf-8")
    assert "execution_managed=True" in source


def test_canonical_pipeline_has_a_kernel_managed_provider_path():
    source = (ROOT / "domain/publishing/canonical_pipeline.py").read_text(encoding="utf-8")
    assert "execution_managed: bool = False" in source
    assert "if pipeline_ledger_key and not execution_managed:" in source
    assert "if execution_managed:" in source
    # The managed branch must not instantiate the publishing ledger.
    managed = source.split("if execution_managed:", 1)[1].split("if ledger and execution:", 1)[0]
    assert "ExecutionLedger" not in managed
    assert "await plugin.publish(" in managed


def test_publish_orchestration_forwards_execution_management_flag():
    source = (ROOT / "services/publish_orchestration.py").read_text(encoding="utf-8")
    assert "execution_managed: bool = False" in source
    assert "execution_managed=execution_managed" in source
