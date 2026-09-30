# IPC-13 — Objective-Aware Workforce & Autonomous Execution Convergence

## Purpose

IPC-13 closes the boundary between the objective-bound decision plane and durable AI Workforce execution.
A workforce run may carry an objective, but it must not invent, replace, or reinterpret objective authority.
The objective context is lineage and success context only; execution authority remains with governance and the Action Kernel.

## Canonical path

`Objective Context -> Decision Evaluation -> Objective-bound Handoff -> Durable Workforce Run -> Lease -> Governed Workflow -> Action Kernel -> Outbox/Connector -> Receipt -> Outcome`

## Implemented controls

- Durable workforce execution now requires `_kdo_objective_context` in the persisted run event before autonomous execution.
- Objective ID must agree across the run event, governance manifest, and objective context.
- Objective context must belong to the execution workspace and remain `active`.
- The autonomous run's human-readable objective must match the durable objective context when present.
- Objective context fingerprints are carried into workforce execution results for lineage.
- New V73 execution context binds workspace + run + objective + success criteria without granting authority.
- Execution context rejects cross-workspace use, run mismatch, objective lineage mismatch, and authority injection.
- No new provider executor was introduced; the existing V71.10/V71.9/BusinessAutomation/Action Kernel path remains authoritative.
- Missing or inconsistent objective lineage fails closed before governed workflow execution.

## Verification

- Objective-aware workforce suite: **20/20 passed**.
- Broader workforce + IPC-12 regression set: **33/33 passed**.
- Python compile check: **passed** for changed services.

## Certification boundary

This is structural and deterministic certification. It does not claim live production proof of PostgreSQL transaction races, process crashes at the provider boundary, network partitions, real connector behavior, or multi-instance failover. Those require live-environment evidence.
