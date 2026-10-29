# Phase 60 — Legacy Execution Convergence

## Goal
Ensure legacy publishing entry points use the canonical Action Kernel execution semantics instead of creating a second external-execution ledger.

## Changes
- `services/publish_worker.py` now delegates publish execution to `execute_governed_publish`.
- `core/publish_outbox.py` remains a compatibility queue/state surface, but its drainer delegates the consequential side effect to `execute_governed_publish`.
- The outbox receipt adapter accepts both legacy top-level receipts and `ActionResult.output` receipts from the Action Kernel.
- `domain/publishing/canonical_pipeline.py` exposes the explicit `execution_managed` boundary and only creates its own ledger when it is the execution authority.
- The affiliate publish action now passes `execution_managed=True` when invoked from the Action Kernel.
- Legacy direct API enqueue behavior remains intact for compatibility; the outbox is now an intent/queue compatibility surface rather than a second external execution authority.

## Invariant
For a Kernel-managed consequential publish:

`ActionIntent -> ActionKernel -> ExecutionLedger -> Publishing Domain -> Provider -> Receipt`

There must be one authoritative execution identity for the external side effect.

## Validation
- Python compilation: PASS
- New Phase 60 convergence tests: 5/5 PASS
- Combined execution/reliability regression subset: 27/27 PASS
- Broader architecture/pipeline subset: 41/42 PASS; the sole failure is the pre-existing archive omission of `.github/workflows/docker-build.yml`, not a Phase 60 code failure.
