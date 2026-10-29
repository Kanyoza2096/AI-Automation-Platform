# KDO Phase 9.5 — Failure / Degradation Policy & Recovery Control

## Purpose

Phase 9.5 defines how KDO responds **as policy** to reliability conditions.

It converts dependency state into a declarative operational decision:

HEALTHY → ALLOW
DEGRADED → DEGRADE or HOLD
UNAVAILABLE → BLOCK
UNKNOWN → HOLD
RECOVERING → RECOVERY_REQUIRED
RECONCILIATION_REQUIRED → RECONCILIATION_REQUIRED
SECURITY_HOLD → SECURITY_HOLD
QUARANTINED → QUARANTINE

These are policy outcomes, not execution commands.

## Authority boundary

The flow remains:

Dependency Evidence
→ Reliability State
→ Failure/Recovery Policy
→ Policy Decision
→ ActionIntent / Governance / Authorization / Action Kernel

The policy layer never:
- calls providers
- authorizes identities
- executes an ActionIntent
- mutates business state
- bypasses reconciliation
- bypasses security hold

## Degraded operation

Degraded behavior is explicit policy, not accidental permissiveness.

Examples:
- read-only inspection may continue during degradation
- low-risk mutation may be allowed only if the policy explicitly permits it
- normal or high-risk mutation is held by default

## Recovery

Recovery is also stateful.

A dependency does not jump blindly from failure to healthy.

Typical path:

UNAVAILABLE
→ RECOVERING
→ HEALTHY

or:

UNAVAILABLE
→ RECOVERING
→ RECONCILIATION_REQUIRED
→ HEALTHY

Security conditions may instead require:

SECURITY_HOLD
→ QUARANTINED
→ RECOVERING
→ HEALTHY

## Critical rule

`PolicyDecision.ALLOW` does **not** mean “execute now.”

It means the reliability layer found no reliability-policy reason to block the operation.

Authorization and the Action Kernel remain authoritative.

## Non-goals

This phase does not implement:
- automated remediation execution
- provider API calls
- durable production policy storage
- live incident orchestration
- disaster recovery
- chaos testing
- live RLS certification
