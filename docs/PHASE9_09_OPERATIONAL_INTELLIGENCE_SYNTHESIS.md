# KDO Phase 9.9 — Operational Intelligence & Reliability Synthesis

## Purpose

Phase 9.9 turns the distributed Phase 9 evidence model into a coherent,
workspace-scoped operational picture.

It answers:

> What is the current operational posture, what evidence supports it, and
> what conditions require attention?

It does **not** answer:

> Should KDO execute an action?

That remains the responsibility of Governance, Authorization, and the Action
Kernel.

## Synthesis precedence

The operational posture is conservative:

```text
QUARANTINED
    ↓
SECURITY_HOLD
    ↓
RECONCILIATION
    ↓
RECOVERY
    ↓
INCIDENT
    ↓
DEGRADED
    ↓
UNKNOWN
    ↓
ATTENTION_REQUIRED
    ↓
NORMAL
```

Higher-severity conditions therefore cannot be hidden by a generic healthy
or degraded summary.

## Evidence

Snapshots preserve evidence references from their contributing signals.

The snapshot is a projection, not a new source of authority.

## Causality boundary

Dominant causes are descriptive references only. This module does not infer
causation from timing, correlation, or co-occurrence.

Causal relationships remain governed by the Phase 9.3 causal graph and its
evidence requirements.

## UI boundary

Command Center may render this snapshot as an operational dashboard, but the
dashboard must not become the source of truth or execution authority.

## Non-goals

This phase does not implement:
- automated remediation
- authorization
- execution
- causal inference
- live metrics collection
- production incident orchestration
- live database/RLS certification
