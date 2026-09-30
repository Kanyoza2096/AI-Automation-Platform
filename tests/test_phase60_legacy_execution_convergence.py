from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_publish_worker_uses_governed_kernel_path():
    source = (ROOT / "services/publish_worker.py").read_text(encoding="utf-8")
    assert "execute_governed_publish" in source
    assert "run_publish(" not in source
    assert "idempotency_key=" in source


def test_publish_outbox_drainer_uses_governed_kernel_path():
    source = (ROOT / "core/publish_outbox.py").read_text(encoding="utf-8")
    process = source[source.index("async def process_item"):]
    assert "execute_governed_publish" in process
    assert "run_publish(" not in process
    assert "idempotency_key=item.idempotency_key" in process


def test_legacy_outbox_normalizes_action_kernel_receipt_output():
    source = (ROOT / "core/publish_outbox.py").read_text(encoding="utf-8")
    assert 'result.get("output")' in source
    assert 'merged.get("post_id")' in source
    assert 'status == "succeeded"' in source


def test_kernel_managed_connector_has_explicit_execution_boundary():
    source = (ROOT / "domain/publishing/canonical_pipeline.py").read_text(encoding="utf-8")
    assert "execution_managed: bool = False" in source
    assert "if pipeline_ledger_key and not execution_managed" in source
    assert "if execution_managed:" in source


def test_affiliate_publish_inside_kernel_is_execution_managed():
    source = (ROOT / "services/action_kernel.py").read_text(encoding="utf-8")
    block = source[source.index("async def affiliate_publish"):source.index("async def affiliate_pause")]
    assert "execution_managed=True" in block
