from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_action_kernel_does_not_own_publishing_parameter_translation():
    source = (ROOT / "services/action_kernel.py").read_text(encoding="utf-8")
    assert "from domain.publishing.action_handler import publish_content" in source
    start = source.index("# ── Publishing domain adapter")
    end = source.index("# ── Website publication", start)
    block = source[start:end]
    assert "from services.publish_orchestration import run_publish" not in block
    assert "base64.b64decode" not in block


def test_publishing_adapter_owns_canonical_pipeline_translation():
    source = (ROOT / "domain/publishing/action_handler.py").read_text(encoding="utf-8")
    assert "from services.publish_orchestration import run_publish" in source
    assert "durable=False" in source
    assert "intent.idempotency_key" in source
    assert "decision_id" in source


def test_publish_contract_remains_registered_at_kernel_boundary():
    source = (ROOT / "services/action_kernel.py").read_text(encoding="utf-8")
    start = source.index('"publish_content"')
    block = source[start:start + 500]
    assert "requires_approval=True" in block
    assert "inline_with_decision=True" in block
    assert "handler=publish_content" in block
