# IPC-18 — Multi-Worker & Workforce Coordination Convergence

## Status
STRUCTURALLY CONVERGED / LIVE PRODUCTION CERTIFICATION PENDING

## Contract
Objective-bound coordination is durable planning state. A worker may coordinate tasks, dependencies and ownership, but coordination never grants execution authority. Each worker must independently enter the existing V73/V71 governed execution path and Action Kernel.

## Guarantees
- Workspace, objective and autonomous-cycle lineage are required.
- Task ownership is explicit; workers cannot claim another worker's task.
- Dependency DAGs are validated and cycles are rejected.
- A task cannot become executable before its dependencies complete and the task is durably claimed.
- Coordination fingerprints detect mutation/tampering.
- Execution context is advisory-only and carries `authority=none` and `execution_effect=none`.
- Cross-workspace, cross-objective and cross-cycle execution contexts are rejected.
- No second executor or governance plane is introduced.

## Verification
46/46 focused cross-phase tests passed, including IPC-12 through IPC-17 and existing workforce chaos coverage.
Python compilation passed.

Live production certification is not claimed for multi-process database races, process crashes during task claims, or real distributed worker failover. Those belong to IPC-19 production certification.
