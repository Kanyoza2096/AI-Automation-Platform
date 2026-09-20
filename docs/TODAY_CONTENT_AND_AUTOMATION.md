
## Governed decision-to-action path

Business Automation does not replace the Condition Engine. The `condition_decision`
action converts a workspace-scoped automation event into a real Condition Engine
`Observation`. The Condition Engine remains responsible for baselines, trends,
anomalies, confidence, risk, cooldowns and action-plan creation.

For concrete content mutations, the Condition Engine's Council-approved executor
now crosses the durable `ExecutionLedger` + `ExternalActionExecutor` boundary
before calling Content Engine mutation methods. This gives those autonomous
mutations idempotency, ownership leases, terminal/uncertain receipts and a
fail-closed path when durable execution is unavailable.

The Content Intelligence pipeline remains evidence-first: intelligence is analyst
synthesis, while retrieved evidence is retained separately and is never promoted
to verified fact merely because an LLM summarized it.
