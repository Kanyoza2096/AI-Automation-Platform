# Reliability Engine

Governed self-healing for the Kanyoza organism.

## Flow

Pulse → Diagnostician → Repair Planner → Guardian/Policy → Executor → Validate → Memory

## Autonomy levels (env `RELIABILITY_AUTONOMY_LEVEL`)

| Value | Behavior |
|-------|----------|
| `0_observe` | Collect only |
| `1_recommend` | Diagnose + plan, no execute |
| `2_reversible` | **Default** — restart, retry, requeue, clear stale, reconcile |
| `3_playbook` | Approved playbooks (disable connector, rollback) need human |
| `4_patch` | Propose patches only — human approval |
| `5_deploy` | Restricted deploy (off by default) |

## What it does NOT do

- Does not auto-write and deploy arbitrary Python
- Does not mutate customer data across workspaces
- Does not bypass Guardian/policy

## Integration

- Boot: `init_reliability_engine(container)` from `main.py`
- Signals: Autonomous Loop `engine_alert` → `handle_engine_alert`
- Events: `reliability.recommendation`, `reliability.escalation`, `reliability.outcome` (platform scope)
