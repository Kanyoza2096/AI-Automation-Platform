# Phase 36.7 — Learning + Real Execution Feedback

## Delivered

The organism now has a bounded learning plane downstream of the durable execution ledger:

`Execution Ledger receipt → ExecutionFeedback → Experience → Memory → World Model`

The learning plane:

- normalizes `succeeded`, `failed`, `uncertain`, and `cancelled` receipts into explicit outcomes;
- preserves **uncertain** as uncertain and never promotes it to success;
- records tenant-scoped episodic experience with execution/provider evidence;
- emits provenance-bearing world facts for execution and action outcomes;
- optionally associates feedback with a canonical organism `Goal`;
- accepts an explicit `goal_progress` proposal only as derived information—the immutable goal remains authoritative;
- rejects cross-workspace goal/receipt combinations;
- is bounded by the existing `MemoryStore` and `WorldModel` limits;
- is failure-isolated from execution authority: learning failure cannot rewrite an authoritative receipt.

`ExternalActionExecutor` can now receive a `LearningEngine` and optional `Goal`. After an authoritative terminal receipt is committed, the executor feeds that receipt into learning. If learning is unavailable, execution truth is retained and the learning error is logged.

## Safety boundary

Learning does not execute actions, grant permissions, mint credentials, modify production code, or convert internal events into external success. Durable execution remains authoritative.
