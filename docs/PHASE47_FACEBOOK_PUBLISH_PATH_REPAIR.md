# Phase 47 — Facebook Publishing Path Repair

## Scope

This phase repairs the three confirmed publishing-path defects identified by the Facebook trace:

1. Autonomous `publish_command` had no consumer.
2. Scheduled posts were not durably reconciled with the publish outbox.
3. Explicit scheduled captions were stored but ignored; workflow provenance also hardcoded `source="scheduled"`.

## Result

### Autonomous

`autonomous_content -> publish_request -> authorization -> publish_command -> AutonomousLoop._handle_publish_command() -> run_publish(durable=True) -> canonical pipeline -> durable outbox -> outbox worker -> provider`.

The autonomous handler requires workspace, brand and decision identity and re-validates the publish authorization before entering the canonical pipeline. It never calls Facebook directly.

### Scheduled

Scheduled workflow context now carries `scheduled_post_id` and the supplied caption. The canonical outbox payload persists the scheduled-post identifier. When the outbox worker reaches a terminal publish or uncertainty outcome, it reconciles the corresponding `scheduled_posts` row.

Queued scheduled rows move to `outbox_pending`, preventing the normal scheduler claim query from selecting them again.

The schema adds `outbox_id`, `started_at`, `completed_at`, and `error` to `scheduled_posts`.

### Provenance

`_trigger_workflow()` now passes its actual `source` into `PostPublishWorkflow` rather than hardcoding `scheduled`. This preserves provenance for retries and non-scheduled callers.

### Explicit scheduled caption

A supplied scheduled caption is treated as an explicit content intent and is used by the workflow instead of silently replacing it with a newly generated caption. The normal validation, rendering and canonical durable publishing controls remain in force.

## Safety

No direct Facebook transport was introduced. All durable autonomous and scheduled publication remains behind the canonical publishing pipeline, governance checks, decision authorization and durable outbox.

## Verification

Full test suite: 296 passed, 1 warning.

The remaining warning is the existing Council deprecation warning.
