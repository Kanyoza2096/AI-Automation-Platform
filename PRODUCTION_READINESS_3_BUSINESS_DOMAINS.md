# Production Readiness — Three Core Business Domains

## Scope

This release hardens and converges:

1. Marketing Intelligence & Content Operations
2. Lead & Customer Operations
3. Autonomous Business Operations

All three use the canonical governed execution substrate. None owns provider execution directly.

## 1. Marketing Intelligence & Content Operations

Objective → intelligence → strategy/generation → governance → publishing → performance → learning.

Production hardening in this release:
- Marketing objectives, briefs and candidates can persist through the existing V70.6 PostgreSQL/Supabase schema.
- Production mode refuses to start without a durable database.
- Objectives/briefs/candidates can be rehydrated after process restart.
- Publishing remains delegated to the Action Kernel.
- Provider success is reconciled from authoritative execution evidence.
- Workspace ownership is enforced during admission and persistence.

## 2. Lead & Customer Operations

Lead → qualification → CRM → communication → outcome → learning.

Existing durable protections are retained:
- workspace-scoped state
- durable event fence
- lead execution lease
- event finalization/recovery
- governed workflow execution
- CRM/customer communication remains behind the canonical execution path
- opted-out and terminal lead states are protected

## 3. Autonomous Business Operations

Objective → observe → understand → plan → govern → execute → verify → recover → evaluate → learn.

Production hardening in this release:
- autonomous run state persists to `agent_autonomous_runs` when a durable DB is configured
- run creation and state transitions persist fingerprints and results
- production execution claims a PostgreSQL worker lease before entering the governed workflow
- only the lease owner may finalize the run
- expired/uncertain runs remain reconciliation-aware
- durable atomic agent-budget reservations remain the execution admission mechanism when configured
- autonomy never calls providers directly; it converges through BusinessAutomation → Action Kernel → durable outbox → connector

## Verification

- Full pytest: **1061 passed, 0 failed**
- Python compile: **COMPILE_OK**
- Existing architecture/certification tests remain green.

## Certification boundary

The repository is production-hardened and regression-clean, but live infrastructure evidence is a separate certification fact. A local automated run cannot truthfully prove:

- the deployed PostgreSQL cluster's actual failover/concurrency behavior
- real worker crash recovery in the deployed environment
- live provider credentials/rate limits
- real external side effects and provider receipts

Those require the controlled staging/production environment. The code intentionally keeps these facts separate from offline certification.
