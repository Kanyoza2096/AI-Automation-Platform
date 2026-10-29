# Phase 37.8 — Automated Trigger Convergence

## Objective
Ensure automated Director/Trend content cannot bypass the canonical publishing spine.

## Canonical lifecycle

Manual API / Scheduled Scheduler / Automation / Trend Intelligence
→ workspace + brand resolution
→ bounded DecisionGate authorization
→ Guardian
→ Metabolism
→ Council when required
→ durable Publish Outbox
→ execution lease + ledger
→ tenant-specific provider connector
→ provider receipt
→ durable finalization
→ Job completion
→ learning feedback

## Safety invariants

- Automated publishers must provide an explicit workspace and brand.
- Director bindings never call a raw Facebook service directly.
- Automated content enters `run_canonical_publish` and therefore converges on the same outbox/execution plane.
- Provider uncertainty remains uncertain until reconciliation.
- Provider credentials are selected by the tenant/brand connector boundary.
- DecisionGate remains the authority boundary; automation cannot self-grant elevated risk.
- A successful enqueue is not reported as provider publication.
- No external live publish is claimed by unit tests.

## Staging certification

Phase 37.8 does not manufacture external evidence. Real certification requires a staging deployment with real Postgres/RLS, Redis and a dedicated Facebook Page/account. The staging evidence plane must record the observed result for manual, scheduled and automated triggers.
