# IPC-16 — Autonomous Objective Execution

IPC-16 bridges a governed IPC-15 replan recommendation into the existing durable V71 workforce runtime.

## Boundary

`Objective Outcome -> Replan Recommendation -> Governed Cycle Decision -> Durable Workforce Run -> Existing V71 Admission -> Action Kernel`

IPC-16 does not create a provider executor, grant authority, mutate policy, or bypass approval. It creates only the next durable run intent, carrying immutable objective/replan lineage.

## Guarantees

- same-workspace objective/replan/run binding;
- separate governance decision required;
- replan fingerprint carried into the next run;
- objective context carried into the durable run event;
- no direct external side effect;
- existing V71.9/V71.10/V71.17 admission and execution remain canonical;
- cycle identity is idempotent on `(workspace_id, source_replan_id)` at the persistence layer.
