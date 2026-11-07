from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
import uuid
from urllib.parse import urlparse

from supabase import create_client
from core.shared_event_bus import SharedEventBus

EXPECTED_HOST = "kkzshurcmcptleyqjgzo.supabase.co"
HANDLER_ID = "kdo.u1.poller.stress.handler.v1"
BACKLOG_SIZE = 120
POLL_TIMEOUT_SECONDS = 240


def _payload_dict(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _competing_poller_worker(url, key, event_ids, event_name, group, result_queue):
    client = create_client(url, key)
    bus = SharedEventBus(client, source_service=group, poll_interval=1)
    handled = []

    def handler(payload):
        marker = payload.get("acceptance_marker")
        if marker:
            handled.append(marker)

    bus.subscribe(
        event_name,
        handler,
        handler_id=HANDLER_ID,
    )
    bus.start()
    expected = set(event_ids)
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    outcome = None
    try:
        while time.monotonic() < deadline:
            handler_rows = client.table("event_handler_deliveries").select(
                "event_id,processed"
            ).eq("consumer_group", group).eq(
                "handler_key", HANDLER_ID
            ).execute().data or []
            delivery_rows = client.table("event_deliveries").select(
                "event_id,processed"
            ).eq("consumer_group", group).execute().data or []
            completed_handlers = {
                row["event_id"] for row in handler_rows
                if row.get("event_id") in expected and row.get("processed") is True
            }
            completed_deliveries = {
                row["event_id"] for row in delivery_rows
                if row.get("event_id") in expected and row.get("processed") is True
            }
            if completed_handlers == expected and completed_deliveries == expected:
                outcome = {
                    "worker": os.getpid(),
                    "handler_invocations": len(handled),
                    "completed_handler_receipts": len(completed_handlers),
                    "completed_group_deliveries": len(completed_deliveries),
                }
                break
            time.sleep(1.0)
        if outcome is None:
            raise RuntimeError(
                f"competing pollers timed out: handlers={len(completed_handlers)}/"
                f"{len(expected)}, deliveries={len(completed_deliveries)}/{len(expected)}"
            )
        result_queue.put(outcome)
    except Exception as exc:
        result_queue.put({"error": f"{type(exc).__name__}: {exc}"})
    finally:
        bus.stop()


def main():
    url = os.environ["STAGING_SUPABASE_URL"].strip()
    key = os.environ["STAGING_SUPABASE_SERVICE_ROLE_KEY"].strip()
    if os.environ.get("KDO_U1_ACCEPTANCE_ENABLED") != "true":
        raise RuntimeError("Fail-closed: KDO_U1_ACCEPTANCE_ENABLED must be true")
    if urlparse(url).hostname != EXPECTED_HOST:
        raise RuntimeError("Refusing to run stress acceptance outside the KDO staging project")
    if not key:
        raise RuntimeError("Staging service-role key is empty")

    client = create_client(url, key)
    ctx = mp.get_context("spawn")
    run_id = uuid.uuid4().hex
    event_name = f"kdo.u1.acceptance.stress.{run_id[:8]}"
    producer = f"github-actions-u1-stress-{run_id[:12]}"
    group = f"u1-poller-compete-{run_id[:8]}"
    created_events = []
    processes = []
    queues = []
    try:
        publisher = SharedEventBus(client, source_service=producer)
        expected_markers = {
            f"stress-{run_id}-{index:03d}" for index in range(BACKLOG_SIZE)
        }
        for marker in sorted(expected_markers):
            if not publisher.publish(event_name, {"acceptance_marker": marker}):
                raise RuntimeError(f"staging stress event publish failed: {marker}")

        rows = client.table("events").select("id,payload").eq(
            "event_type", event_name
        ).eq("source_service", producer).execute().data or []
        marker_to_id = {
            _payload_dict(row.get("payload")).get("acceptance_marker"): str(row["id"])
            for row in rows
        }
        if set(marker_to_id) != expected_markers:
            raise RuntimeError(
                f"published event inventory mismatch: expected {BACKLOG_SIZE}, "
                f"found {len(marker_to_id)}"
            )
        event_ids = [marker_to_id[marker] for marker in sorted(expected_markers)]
        created_events.extend(event_ids)

        # Two independent OS processes deliberately share one consumer group.
        # Durable group/handler claims must prevent duplicate execution while
        # each poller's local cursor advances through the 120-event backlog.
        for _ in range(2):
            result_queue = ctx.Queue()
            process = ctx.Process(
                target=_competing_poller_worker,
                args=(url, key, event_ids, event_name, group, result_queue),
            )
            process.start()
            processes.append(process)
            queues.append(result_queue)

        results = []
        for process, result_queue in zip(processes, queues):
            process.join(POLL_TIMEOUT_SECONDS + 15)
            if process.is_alive():
                process.terminate()
                process.join()
                raise RuntimeError("competing poller process exceeded its timeout")
            if process.exitcode != 0:
                raise RuntimeError(f"competing poller exited with code {process.exitcode}")
            result = result_queue.get(timeout=5)
            if result.get("error"):
                raise RuntimeError(result["error"])
            results.append(result)

        total_invocations = sum(item["handler_invocations"] for item in results)
        if total_invocations != BACKLOG_SIZE:
            raise RuntimeError(
                f"duplicate or missing handler execution: expected {BACKLOG_SIZE}, "
                f"observed {total_invocations}"
            )

        print(json.dumps({
            "status": "PASS",
            "backlog_size": BACKLOG_SIZE,
            "poll_page_size": SharedEventBus.MAX_EVENTS_PER_POLL,
            "competing_processes": 2,
            "consumer_group": group,
            "total_handler_invocations": total_invocations,
            "checks": [
                "120 matching events exceed the 50-event poll page",
                "two independent poller processes compete in one consumer group",
                "all durable handler receipts reach processed state",
                "all group delivery receipts reach processed state",
                "exactly one handler invocation is observed per test event",
            ],
            "workers": results,
        }, indent=2))
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        # Remove only this run's uniquely tagged events; dependent receipts
        # are removed by the staging schema's event foreign-key cleanup.
        for event_id in created_events:
            try:
                client.table("events").delete().eq("id", event_id).execute()
            except Exception as exc:
                print(
                    f"WARNING: cleanup failed for this run's event {event_id}: "
                    f"{type(exc).__name__}: {exc}"
                )


if __name__ == "__main__":
    main()
