# Phase 94 — Command Center Truth Convergence

## Scope

This phase closes three remaining Mission Control evidence defects observed in the operator surface:

1. **System health key drift** — gateway used `system` while the frontend contract consumed `systemHealth`.
2. **Critical posture drift** — the gateway critical-source set still referenced `system`, so system-health failure was not classified as a critical failure even after the source key was corrected.
3. **Event presentation loss** — recent event records carried `event_name` and payload data, but the frontend did not read `event_name`; event records also lacked a deterministic operator summary.

## Changes

- Canonicalized Command Center source key to `systemHealth` for `/system/health`.
- Canonicalized the Command Center critical set to `systemHealth`.
- Added a deterministic backend `summary` field to both event API wrappers.
  - Producer-supplied `summary`, `message`, or `description` wins.
  - Otherwise only observed `event_name` and optional `status` are described.
  - No business meaning is invented from missing payload data.
- Frontend event rendering now recognizes `event_name` and prefers the normalized `summary`.
- Honest fallback wording now distinguishes a missing producer summary from a missing event record.
- Added Phase 94 regression tests for gateway source/critical-key alignment and event summary behavior.

## Health interpretation

The `SERVICES 0/2` display is **not cosmetically overridden**. `/api/v1/system/health` exposes the actual service/connector observations returned by the runtime `HealthChecker`; the Sidebar counts those records. A `0/2` result therefore means two service records were observed and neither was reported healthy. It is not equivalent to “backend unreachable.”

Local reconstruction without the live staging configuration currently produces an empty plugin registry, so it cannot prove which two runtime services were present in the user's live screenshot. No false live health claim is made here.

## Event interpretation

The frontend previously displayed generic `EVENT` because it did not include `event_name` in its event-type key list. That is now corrected. Event summaries are deterministic and evidence-bound; they are not AI-generated narrative text.

## Certification

- Backend: **354 passed, 1 existing Pydantic deprecation warning** when run from `backend/kdo19`.
- Gateway Phase 94 static contract test: **PASS**.
- Gateway syntax gate: **PASS**.
- Frontend auth: **PASS**.
- Frontend governed mutation certification: **PASS (5/5)**.
- Frontend transaction reconciliation: **PASS (12/12)**.
- Frontend event reconciliation: **PASS**.
- Frontend Phase 67 route drift: **0 failures, 7 existing parameter-name warnings**.
- Frontend Phase 69 runtime contract: **PASS (5/5)**.
- Frontend Phase 70 authority/posture: **PASS (8/8)**.

A production/live runtime certification still requires real staging credentials and reachable services; this phase does not substitute static tests for live evidence.
