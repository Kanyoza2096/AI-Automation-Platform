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



def test_event_consumer_accepts_supabase_jsonb_payload_dict():
    from core.event_consumer import EventConsumer

    consumer = EventConsumer()
    received = []
    processed = []
    consumer._handle_director_job_recovery_request = (
        lambda payload: received.append(payload) or True
    )
    consumer._mark_processed = lambda event_id: processed.append(event_id)

    payload = {
        "trigger": "automation_job_failure_threshold",
        "recovery_mode": "bounded_advisory_sweep",
        "automatic_retry": False,
        "publication_replay_allowed": False,
        "correlation_id": "job-recovery-jsonb-test",
        "failed_jobs": 9,
    }
    consumer._process_event({
        "id": "event-jsonb-test",
        "event_name": "director.automation_job_recovery.requested",
        "payload": payload,
        "source_service": "job_stats_service",
    })

    assert received == [payload]
    assert processed == ["event-jsonb-test"]


def test_event_consumer_still_accepts_legacy_json_string_payload():
    from core.event_consumer import EventConsumer
    import json

    consumer = EventConsumer()
    received = []
    consumer._handle_director_job_recovery_request = (
        lambda payload: received.append(payload) or True
    )
    consumer._mark_processed = lambda _event_id: None

    payload = {
        "trigger": "automation_job_failure_threshold",
        "recovery_mode": "bounded_advisory_sweep",
        "automatic_retry": False,
        "publication_replay_allowed": False,
        "correlation_id": "job-recovery-string-test",
        "failed_jobs": 9,
    }
    consumer._process_event({
        "id": "event-string-test",
        "event_name": "director.automation_job_recovery.requested",
        "payload": json.dumps(payload),
        "source_service": "job_stats_service",
    })

    assert received == [payload]



def test_event_consumer_polling_excludes_stale_event_backlog():
    consumer = (ROOT / "core/event_consumer.py").read_text(encoding="utf-8")

    assert "MAX_EVENT_AGE_HOURS = 24" in consumer
    assert '.gte("created_at", event_cutoff)' in consumer
