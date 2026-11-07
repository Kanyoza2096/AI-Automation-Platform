from __future__ import annotations

import json
import multiprocessing as mp
import os
import socket
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from supabase import create_client
from core.shared_event_bus import SharedEventBus

EVENT_NAME = "kdo.u1.acceptance.probe"
HANDLER_ID = "kdo.u1.acceptance.noop.v1"
EXPECTED_HOST = "kkzshurcmcptleyqjgzo.supabase.co"
WORKSPACE_ID = "kdo-engineering-staging"


def _handler(_payload, *, fail_first=False, delay=0.0):
    if delay:
        time.sleep(delay)
    if fail_first:
        raise RuntimeError("intentional U1 acceptance retry probe")


def _worker(url, key, event_id, group, mode, start_gate, result_queue):
    try:
        client = create_client(url, key)
        event_result = client.table("events").select("*").eq("id", event_id).limit(1).execute()
        if not event_result.data:
            raise RuntimeError(f"acceptance event not found: {event_id}")
        event = event_result.data[0]
        bus = SharedEventBus(client, source_service=group)
        fail = mode == "fail"
        delay = 0.35 if mode == "race" else 0.0
        bus.subscribe(
            EVENT_NAME,
            lambda payload: _handler(payload, fail_first=fail, delay=delay),
            handler_id=HANDLER_ID,
        )
        if start_gate is not None:
            start_gate.wait(timeout=20)
        if not bus._ensure_delivery(event_id, EVENT_NAME):
            raise RuntimeError(f"could not ensure group receipt for {group}")
        claim = bus._claim_delivery(event_id)
        if claim is None:
            result_queue.put({"group": group, "result": "not_claimed"})
            return
        delivery_id, claim_token = claim
        bus._dispatch_event(event, delivery_id=delivery_id, claim_token=claim_token)
        group_rows = client.table("event_deliveries").select("*").eq(
            "event_id", event_id
        ).eq("consumer_group", group).limit(1).execute().data or []
        handler_rows = client.table("event_handler_deliveries").select("*").eq(
            "event_id", event_id
        ).eq("consumer_group", group).eq("handler_key", HANDLER_ID).limit(1).execute().data or []
        result_queue.put({
            "group": group,
            "result": "dispatched",
            "group_processed": bool(group_rows and group_rows[0].get("processed")),
            "handler_processed": bool(handler_rows and handler_rows[0].get("processed")),
            "handler_error": handler_rows[0].get("processing_error") if handler_rows else "missing",
        })
    except Exception as exc:
        result_queue.put({"group": group, "error": f"{type(exc).__name__}: {exc}"})


def _new_event(client, marker):
    event_id = uuid.uuid4().hex
    correlation_id = uuid.uuid4().hex
    payload = {
        "acceptance_marker": marker,
        "workspace_id": WORKSPACE_ID,
        "correlation_id": correlation_id,
        "_nerve": {
            "event_id": event_id,
            "event_name": EVENT_NAME,
            "source": "github-actions-u1-acceptance",
            "workspace_id": WORKSPACE_ID,
            "correlation_id": correlation_id,
            "schema_version": "1.0",
        },
    }
    client.table("events").insert({
        "id": event_id,
        "event_type": EVENT_NAME,
        "payload": payload,
        "source_service": "github-actions-u1-acceptance",
        "workspace_id": WORKSPACE_ID,
        "correlation_id": payload["correlation_id"],
        "source_instance": f"acceptance-producer:{uuid.uuid4().hex}",
        "processed": False,
        "schema_version": "1.0",
    }).execute()
    return event_id


def _run_one(ctx, url, key, event_id, group, mode):
    queue = ctx.Queue()
    process = ctx.Process(target=_worker, args=(url, key, event_id, group, mode, None, queue))
    process.start()
    process.join(45)
    if process.is_alive():
        process.terminate()
        process.join()
        raise RuntimeError(f"consumer process timed out for {group}")
    if process.exitcode != 0:
        raise RuntimeError(f"consumer process exited {process.exitcode} for {group}")
    result = queue.get(timeout=5)
    if "error" in result:
        raise RuntimeError(result["error"])
    return result


def main():
    url = os.environ["STAGING_SUPABASE_URL"].strip()
    key = os.environ["STAGING_SUPABASE_SERVICE_ROLE_KEY"].strip()
    if os.environ.get("KDO_U1_ACCEPTANCE_ENABLED") != "true":
        raise RuntimeError("Fail-closed: set KDO_U1_ACCEPTANCE_ENABLED=true to authorize staging test")
    host = urlparse(url).hostname
    if host != EXPECTED_HOST:
        raise RuntimeError(f"Refusing non-staging Supabase host: {host!r}")
    if not key:
        raise RuntimeError("Staging service-role key is empty")

    client = create_client(url, key)
    ctx = mp.get_context("spawn")
    created_events = []
    results = []
    try:
        # Independent consumer groups must each persist their own handler receipt.
        fanout_event = _new_event(client, f"fanout-{uuid.uuid4().hex}")
        created_events.append(fanout_event)
        for group in ("u1-acceptance-fanout-a", "u1-acceptance-fanout-b"):
            result = _run_one(ctx, url, key, fanout_event, group, "success")
            if not (result.get("group_processed") and result.get("handler_processed")):
                raise RuntimeError(f"fan-out receipt did not complete: {result}")
            results.append(result)

        # Two independent processes race for the same consumer-group delivery.
        race_event = _new_event(client, f"race-{uuid.uuid4().hex}")
        created_events.append(race_event)
        gate = ctx.Event()
        queue = ctx.Queue()
        group = "u1-acceptance-race"
        processes = [
            ctx.Process(target=_worker, args=(url, key, race_event, group, "race", gate, queue))
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        gate.set()
        for process in processes:
            process.join(45)
            if process.is_alive():
                process.terminate()
                process.join()
                raise RuntimeError("competing consumer process timed out")
            if process.exitcode != 0:
                raise RuntimeError(f"competing consumer exited {process.exitcode}")
        race_results = [queue.get(timeout=5) for _ in processes]
        if sum(item.get("result") == "dispatched" for item in race_results) != 1:
            raise RuntimeError(f"expected exactly one group-claim winner, got {race_results}")
        if any(item.get("error") for item in race_results):
            raise RuntimeError(f"competing consumer error: {race_results}")
        persisted = client.table("event_handler_deliveries").select("*").eq(
            "event_id", race_event
        ).eq("consumer_group", group).eq("handler_key", HANDLER_ID).execute().data or []
        if len(persisted) != 1 or not persisted[0].get("processed"):
            raise RuntimeError("race did not produce one completed durable handler receipt")
        results.extend(race_results)

        # Force a failed handler lease to expire, then verify the same receipt retries.
        retry_event = _new_event(client, f"retry-{uuid.uuid4().hex}")
        created_events.append(retry_event)
        retry_group = "u1-acceptance-retry"
        failed = _run_one(ctx, url, key, retry_event, retry_group, "fail")
        if failed.get("handler_processed") or not failed.get("handler_error"):
            raise RuntimeError(f"intentional failure was not recorded: {failed}")
        expired = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        retry_due = datetime.now(timezone.utc).isoformat()
        client.table("event_deliveries").update({
            "claimed_at": expired,
            "next_attempt_at": retry_due,
        }).eq(
            "event_id", retry_event
        ).eq("consumer_group", retry_group).eq("processed", False).execute()
        client.table("event_handler_deliveries").update({"claimed_at": expired}).eq(
            "event_id", retry_event
        ).eq("consumer_group", retry_group).eq("handler_key", HANDLER_ID).eq(
            "processed", False
        ).execute()
        retried = _run_one(ctx, url, key, retry_event, retry_group, "success")
        if not (retried.get("group_processed") and retried.get("handler_processed")):
            raise RuntimeError(f"failed handler did not recover on retry: {retried}")
        results.extend([failed, retried])

        print(json.dumps({"status": "PASS", "checks": [
            "independent consumer-group fan-out",
            "same-group competing consumer claim exclusivity",
            "durable handler receipt after success",
            "failed handler retry after lease expiry",
        ], "results": results}, indent=2))
    finally:
        # Only remove the exact event IDs created by this run; FK cascades remove their receipts.
        for event_id in created_events:
            try:
                client.table("events").delete().eq("id", event_id).execute()
            except Exception as exc:
                print(f"WARNING: cleanup failed for test event {event_id}: {type(exc).__name__}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
