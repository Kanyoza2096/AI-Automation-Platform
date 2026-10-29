# R8 — Business Activation Review Loop

## Scope

R8 implements Business Activation Phase 4 only: operator review of extracted business hours and refund/return facts.

## Flow

```text
Extraction
  -> Proposed Facts / Clarifications
  -> Operator edits or answers one fact type
  -> Scoped re-extraction
  -> Review again
  -> Confirm all (session decision only)
```

## Safety boundary

R8 does **not** persist or confirm facts in the Knowledge plane. `Confirm all` is a local review decision. Runtime-readable knowledge remains impossible until the Phase 5 storage boundary writes a confirmed, versioned fact.

## Backend contract

`POST /api/v1/business-activation/review/re-extract` accepts:

- `workspace_id`
- original `operator_text`
- one `fact_type` (`business_hours` or `refund_policy`)
- `correction_text` containing the operator correction/clarification answer

The response is the normal extraction result. Only the requested fact type is re-extracted. No persistence, confirmation, execution, or authorization authority is introduced.

## Frontend review rules

- Facts are shown in plain-language review cards with source spans.
- Each fact can be corrected independently.
- Clarifications accept free-form answers and re-extract only that fact type.
- `Confirm all` is disabled while clarifications remain.
- Re-extraction clears the session confirmation because the proposed evidence changed.
- The UI explicitly states that Phase 5 is responsible for Knowledge persistence.

## Verification

- Business Activation contract tests: 7/7 PASS.
- Cumulative R7 runtime regression: 19/19 PASS.
- Python compilation: PASS.
- Frontend source syntax: TypeScript parser reached dependency-resolution errors only; no Business Activation syntax errors remain.
- Full frontend build is not claimed because dependencies are not installed in the archive.
