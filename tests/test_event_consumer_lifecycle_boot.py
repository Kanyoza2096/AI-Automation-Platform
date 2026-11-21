from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_lifecycle_starts_durable_event_consumer_with_service_role_client():
    lifecycle = (ROOT / "lifecycle.py").read_text(encoding="utf-8")

    assert 'name="EventConsumer"' in lifecycle
    assert "action=partial(_boot_event_consumer, container)" in lifecycle
    assert "from core.event_consumer import init_event_consumer" in lifecycle
    assert "get_service_client_from_settings" in lifecycle
    assert "supabase_client=client" in lifecycle
    assert "shared_bus=publisher" in lifecycle
    assert '"EventConsumer", consumer.stop' in lifecycle


def test_event_consumer_boot_is_non_critical_and_has_shutdown_rollback():
    lifecycle = (ROOT / "lifecycle.py").read_text(encoding="utf-8")
    start = lifecycle.index('name="EventConsumer"')
    block = lifecycle[start:start + 400]

    assert "rollback=partial(_stop_event_consumer, container)" in block
    assert "critical=False" in block


def test_event_consumer_remains_platform_admission_handler_not_job_executor():
    consumer = (ROOT / "core/event_consumer.py").read_text(encoding="utf-8")
    start = consumer.index("def _handle_director_job_recovery_request")
    handler = consumer[start:start + 12000]

    assert "admit_platform_recovery_mission" in handler
    assert "automatic_retry" in handler
    assert "publication_replay_allowed" in handler
    assert "retry_job(" not in handler
    assert "publish_job(" not in handler
