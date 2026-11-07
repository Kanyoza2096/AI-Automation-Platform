from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from supabase import create_client
from core.shared_event_bus import SharedEventBus

EXPECTED_HOST = "kkzshurcmcptleyqjgzo.supabase.co"
HANDLER_ID = "kdo.u1.poller.acceptance.handler.v1"
POLL_TIMEOUT_SECONDS = 35
WORKSPACE_ID = "kdo-engineering-staging"


def _payload_dict(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _find_published_event(client, event_name, producer, marker):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        rows = client.table("events").select("*").eq(
            "event_type", event_name
        ).eq("source_service", producer).eq("workspace_id", WORKSPACE_ID).order(
            "created_at", desc=True
        ).limit(20).execute().data or []
        for event in rows:
            if _payload_dict(event.get("payload")).get("acceptance_marker") == marker:
                return event
        time.sleep(0.25)
    raise RuntimeError("published acceptance event was not found in staging")


def _poller_worker(url, key, event_id, event_name, group, mode, result_queue):
    client = create_client(url, key)
    bus = SharedEventBus(client, source_service=group, poll_interval=1)
    attempts = []

    def handler(payload):
        attempts.append(payload.get("acceptance_marker"))
        if mode == "fail_once":
            raise RuntimeError("intentional U1 poller retry probe")

    bus.subscribe(event_name, handler, handler_id=HANDLER_ID)
    bus.start()
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    outcome = None
    try:
        while time.monotonic() < deadline:
            group_rows = client.table("event_deliveries").select("*").eq(
                "event_id", event_id
            ).eq("consumer_group", group).limit(1).execute().data or []
            handler_rows = client.table("event_handler_deliveries").select("*").eq(
                "event_id", event_id
            ).eq("consumer_group", group).eq(
                "handler_key", HANDLER_ID
            ).limit(1).execute().data or []
            group_row = group_rows[0] if group_rows else {}
            handler_row = handler_rows[0] if handler_rows else {}

            if handler_row.get("processed") is True and group_row.get("processed") is True:
                outcome = {
                    "group": group, "status": "processed",
                    "handler_attempts": len(attempts),
                    "group_processed": True, "handler_processed": True,
                }
                break
            if mode == "fail_once" and handler_row.get("processing_error"):
                outcome = {
                    "group": group, "status": "failed_as_expected",
                    "handler_attempts": len(attempts),
                    "group_processed": bool(group_row.get("processed")),
                    "handler_processed": bool(handler_row.get("processed")),
                    "handler_error": handler_row.get("processing_error"),
                }
                break
            time.sleep(0.2)
        if outcome is None:
            raise RuntimeError(f"poller timed out waiting for receipts: {group}")
        result_queue.put(outcome)
    except Exception as exc:
        result_queue.put({"group": group, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        bus.stop()


def _run_poller_worker(ctx, url, key, event_id, event_name, group, mode):
    queue = ctx.Queue()
    process = ctx.Process(
        target=_poller_worker,
        args=(url, key, event_id, event_name, group, mode, queue),
    )
    process.start()
    process.join(POLL_TIMEOUT_SECONDS + 10)
    if process.is_alive():
        process.terminate()
        process.join()
        raise RuntimeError(f"poller worker timed out for {group}")
    if process.exitcode != 0:
        raise RuntimeError(f"poller worker exited {process.exitcode} for {group}")
    result = queue.get(timeout=5)
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result


def _publish_test_event(client, event_name, producer, marker):
    publisher = SharedEventBus(client, source_service=producer)
    if not publisher.publish(event_name, {"acceptance_marker": marker}, workspace_id=WORKSPACE_ID):
        raise RuntimeError("SharedEventBus.publish returned false for staging probe")
    return _find_published_event(client, event_name, producer, marker)


def main():
    url = os.environ["STAGING_SUPABASE_URL"].strip()
    key = os.environ["STAGING_SUPABASE_SERVICE_ROLE_KEY"].strip()
    if os.environ.get("KDO_U1_ACCEPTANCE_ENABLED") != "true":
        raise RuntimeError("Fail-closed: KDO_U1_ACCEPTANCE_ENABLED must be true")
    if urlparse(url).hostname != EXPECTED_HOST:
        raise RuntimeError("Refusing to run poller acceptance outside the KDO staging project")
    if not key:
        raise RuntimeError("Staging service-role key is empty")

    client = create_client(url, key)
    ctx = mp.get_context("spawn")
    run_id = uuid.uuid4().hex
    event_name = f"kdo.u1.acceptance.poller.{run_id}"
    producer = f"github-actions-u1-poller-{run_id[:12]}"
    created_events = []
    results = []
    try:
        # Publish through the real SharedEventBus.publish path, then let each
        # independent consumer group's background poll loop discover the event.
        fanout_marker = f"poller-fanout-{run_id}"
        fanout_event = _publish_test_event(client, event_name, producer, fanout_marker)
        fanout_id = str(fanout_event["id"])
        created_events.append(fanout_id)
        for group in (f"u1-poller-fanout-a-{run_id[:8]}", f"u1-poller-fanout-b-{run_id[:8]}"):
            result = _run_poller_worker(ctx, url, key, fanout_id, event_name, group, "success")
            if not (result.get("group_processed") and result.get("handler_processed")):
                raise RuntimeError(f"poller fan-out did not complete: {result}")
            results.append(result)

        # Prove that a real poller records a handler failure, then recovers the
        # same durable receipt after its lease expires.
        retry_marker = f"poller-retry-{run_id}"
        retry_event = _publish_test_event(client, event_name, producer, retry_marker)
        retry_id = str(retry_event["id"])
        created_events.append(retry_id)
        retry_group = f"u1-poller-retry-{run_id[:8]}"
        failed = _run_poller_worker(ctx, url, key, retry_id, event_name, retry_group, "fail_once")
        if failed.get("status") != "failed_as_expected" or not failed.get("handler_error"):
            raise RuntimeError(f"poller did not record the intentional failure: {failed}")

        expired = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        retry_due = datetime.now(timezone.utc).isoformat()
        client.table("event_deliveries").update({
            "claimed_at": expired,
            "next_attempt_at": retry_due,
        }).eq(
            "event_id", retry_id
        ).eq("consumer_group", retry_group).eq("processed", False).execute()
        client.table("event_handler_deliveries").update({"claimed_at": expired}).eq(
            "event_id", retry_id
        ).eq("consumer_group", retry_group).eq("handler_key", HANDLER_ID).eq(
            "processed", False
        ).execute()

        recovered = _run_poller_worker(ctx, url, key, retry_id, event_name, retry_group, "success")
        if not (recovered.get("group_processed") and recovered.get("handler_processed")):
            raise RuntimeError(f"poller retry did not complete: {recovered}")
        results.extend([failed, recovered])

        print(json.dumps({
            "status": "PASS",
            "checks": [
                "event published through SharedEventBus.publish",
                "background poll loop discovers and dispatches the published event",
                "independent poller consumer groups each persist receipts",
                "poller records handler failure and retries the same receipt after lease expiry",
            ],
            "results": results,
        }, indent=2))
    finally:
        # Delete only the exact events created by this run; related receipts cascade.
        for event_id in created_events:
            try:
                client.table("events").delete().eq("id", event_id).execute()
            except Exception as exc:
                print(f"WARNING: cleanup failed for test event {event_id}: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
