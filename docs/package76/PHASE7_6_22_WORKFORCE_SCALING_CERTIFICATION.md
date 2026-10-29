# KDO Phase 7.7 — Governed Workforce Scaling & Coordination

## Scope
Adds a governed scaling-decision layer above the Phase 7.6 Worker Registry.

## Architectural contract
ScalingCoordinator:
- observes queue/capacity/failure/safety signals;
- evaluates a workspace-scoped ScalingPolicy;
- produces a deterministic, tamper-evident ScalingDecision;
- can recommend HOLD / NO_CHANGE / SCALE_UP / SCALE_DOWN;
- never provisions workers;
- never mutates infrastructure;
- never authorizes actions;
- never invokes the Action Kernel;
- never calls an external provider;
- never changes worker identity, capability permission, or governance policy.

## Boundaries
Worker Registry remains the inventory/lifecycle authority.
Worker Selection remains responsible for selecting an eligible existing worker.
Scaling decides only the desired pool capacity; it does not materialize instances.
A future provisioning boundary must independently enforce governance, credentials,
resource budgets, deployment safety, health checks, and rollback.

## Safety rules
- emergency stop => HOLD
- capability drift => HOLD
- failure rate above policy safety limit => HOLD
- cooldown => HOLD
- policy min/max cannot exceed pool min/max
- scale-up is bounded by max instances and max step
- scale-down respects minimum instances and can be disabled
- workspace isolation is enforced
- policy, observation, and decision fingerprints are verified
- scaling decisions are advisory only

## Validation
- Focused/adjacent workforce tests: 28 passed
- Full architecture suite: 458 passed
- compileall: PASS
- authority-pattern scan: no Action Kernel / authorization / provider execution / provisioning call path in scaling module

## Certification conclusion
PHASE 7.7 — GOVERNED WORKFORCE SCALING CONTROL: PASS

This is an architecture/control-plane certification, not infrastructure autoscaling certification.
Actual worker provisioning remains intentionally outside this component.
