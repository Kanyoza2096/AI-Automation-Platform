# Phase 43 — Adaptive Sales Orchestration

KDO can now select one bounded next sales operation from trusted customer-response signals.

The orchestrator is deterministic and allow-listed. It does not execute connectors directly.
It delegates every consequential operation to the Action Kernel, preserving workspace scope,
idempotency, provider authorization, approval requirements, and the uncertain/reconciliation model.

Supported signals currently include purchase_interest, price_request, meeting_request, objection,
general_inquiry, and opt_out. This is policy-based orchestration, not yet unrestricted AI planning.
