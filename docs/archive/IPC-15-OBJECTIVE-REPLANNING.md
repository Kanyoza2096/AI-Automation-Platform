# IPC-15 — Objective-Aware Autonomous Replanning & Lifecycle Convergence

## Purpose

IPC-15 converts verified IPC-14 objective outcomes into governed replanning recommendations. It does **not** let learning, intelligence, or a worker mutate objective lifecycle, policy, approvals, autonomy, or execution authority.

## Canonical loop

`Objective → Decision → Workforce → Execution → Independent Outcome Evidence → IPC-14 Verification → IPC-15 Replan Recommendation → Governance Decision → Objective Lifecycle / New Decision Cycle`

## Dispositions

- `continue`: verified progress; start/continue another governed cycle.
- `adapt`: verified regression; request a new decision cycle while existing policy remains authoritative.
- `pause`: lifecycle state is paused pending governed intervention.
- `escalate`: uncertain evidence or non-active lifecycle requires review.
- `recover`: reserved for governed recovery paths.

## Safety boundaries

- Replanning is advisory and has `authority=none`.
- Objective/workspace identity is bound to the IPC-14 verification.
- Lifecycle transitions are validated against an explicit state machine.
- Recommendations are fingerprinted; tampering is rejected.
- A lifecycle mutation requires a separate governed decision bound to the same workspace/objective.
- Lifecycle admission never grants data-plane execution authority.
- Replanning cannot modify policy, approval requirements, autonomy, connector permissions, or execution effects.
- Objective state changes are rejected if the durable objective changed after recommendation creation.

## Validation

Focused IPC-12/13/14/15 regression suite: **25/25 passed**.

Python compilation for the new service: passed.

This archive-level certification does not claim live production proof for concurrent PostgreSQL transitions, process crashes, multi-instance failover, or live provider/network conditions.
