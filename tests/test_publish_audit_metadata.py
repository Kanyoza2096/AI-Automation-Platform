import json
from pathlib import Path
from types import SimpleNamespace

from workflows.post_workflow import _build_publish_audit_metadata


def test_publish_audit_metadata_records_decisions_and_trace_identifiers():
    validation = SimpleNamespace(
        is_approved=True,
        status=SimpleNamespace(value="approved"),
    )
    context = {
        "correlation_id": "corr-123",
        "job_id": "pub-123",
        "scheduled_post_id": "sched-123",
        "source": "brand_scheduler",
        "prompt_policy_version": "kdo-caption-contract-v1",
        "prompt_sha256": "a" * 64,
        "content_result": {"provider": "cloudflare_ai", "model": "model-x"},
        "knowledge_evidence": [
            {"source_id": "kb-1", "text": "private retrieved passage"},
            {"source_id": "kb-2", "text": "another private passage"},
        ],
        "evidence_validation": {
            "ok": True,
            "supporting_source_ids": ["kb-1"],
        },
        "validation_result": validation,
        "semantic_review": {
            "approved": True,
            "status": "approved",
            "request_id": "review-1",
            "provider": "review-provider",
            "model": "review-model",
            "supporting_source_ids": ["kb-1"],
        },
        "publish_result": {
            "action_intent_id": "intent-1",
            "action_execution_id": "execution-1",
        },
        "base_prompt": "private full prompt",
        "caption": "private published caption",
    }

    metadata = _build_publish_audit_metadata(context, decision_id="decision-1")

    assert metadata["correlation_id"] == "corr-123"
    assert metadata["automation_job_id"] == "pub-123"
    assert metadata["decision_id"] == "decision-1"
    assert metadata["prompt_sha256"] == "a" * 64
    assert metadata["evidence_source_ids"] == ["kb-1", "kb-2"]
    assert metadata["evidence_validation"]["ok"] is True
    assert metadata["policy_validation"] == {
        "approved": True,
        "status": "approved",
    }
    assert metadata["semantic_review"]["request_id"] == "review-1"
    assert metadata["publish_action"]["execution_id"] == "execution-1"

    serialized = json.dumps(metadata)
    assert "private full prompt" not in serialized
    assert "private published caption" not in serialized
    assert "private retrieved passage" not in serialized
    assert "another private passage" not in serialized


def test_publish_audit_metadata_handles_missing_optional_context():
    metadata = _build_publish_audit_metadata({}, decision_id=None)

    assert metadata["trace_schema_version"] == 1
    assert metadata["decision_id"] is None
    assert metadata["evidence_source_ids"] == []
    assert metadata["policy_validation"] == {
        "approved": False,
        "status": None,
    }
    assert metadata["semantic_review"]["approved"] is False
    assert metadata["publish_action"] == {
        "intent_id": None,
        "execution_id": None,
    }
    json.dumps(metadata)


def test_publish_audit_schema_migration_is_additive_and_idempotent():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "020_add_publish_audit_metadata.sql"
    ).read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS decision_id TEXT" in migration
    assert "ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb" in migration
    assert "CREATE INDEX IF NOT EXISTS post_history_decision_id_idx" in migration
    assert "CREATE INDEX IF NOT EXISTS post_history_correlation_id_idx" in migration


def test_publish_step_forwards_correlation_id_to_governed_action():
    source = (
        Path(__file__).resolve().parents[1]
        / "workflows"
        / "post_workflow.py"
    ).read_text(encoding="utf-8")
    publish_call = source.split("result = await execute_governed_publish(", 1)[1].split(
        "\n        )", 1
    )[0]

    assert 'correlation_id=context.get("correlation_id")' in publish_call
    assert "automation_job_id=job_id" in publish_call


def test_post_history_write_includes_audit_columns():
    source = (
        Path(__file__).resolve().parents[1]
        / "workflows"
        / "post_workflow.py"
    ).read_text(encoding="utf-8")
    history_write = source.split("async def _write_post_history", 1)[1]

    assert '"decision_id": decision_id' in history_write
    assert '"metadata": audit_metadata' in history_write
