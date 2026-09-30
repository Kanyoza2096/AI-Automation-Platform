from __future__ import annotations

from pathlib import Path

from core.webhook_security import WebhookIdempotencyStore


def test_webhook_claim_is_not_completion_and_can_complete() -> None:
    store = WebhookIdempotencyStore(ttl_seconds=60, max_size=100, db=None)
    key = "github:delivery-1"

    assert store.claim(key) is True
    assert store.claim(key) is False

    store.complete(key)
    assert store.claim(key) is False


def test_failed_in_process_delivery_is_retryable_after_ttl() -> None:
    store = WebhookIdempotencyStore(ttl_seconds=1, max_size=100, db=None)
    key = "fb:message-1"

    assert store.claim(key) is True
    # Simulate the processing lease expiring without completion.
    store._processing[key] = 0.0  # noqa: SLF001 - deterministic unit-test clock injection

    assert store.claim(key) is True


def test_webhook_migration_uses_processing_and_completed_states() -> None:
    migration = Path("migrations/034_webhook_retry_authority.sql").read_text()
    assert "status TEXT NOT NULL DEFAULT 'processing'" in migration
    assert "status IN ('processing', 'completed')" in migration
    assert "complete_webhook_delivery" in migration
    assert "status = 'processing'" in migration
    assert "expires_at < NOW()" in migration
