# R12 — Business Activation Agent Runtime

## Boundary
R12 closes the gap between confirmed Business Activation knowledge and a runtime answer. The runtime answer boundary is read-only and may consume only confirmed, current Knowledge Plane facts.

## Flow
Natural-language teaching → extraction → review → explicit confirmation → Knowledge Plane → runtime read → grounded answer.

## Runtime states
- `GROUNDED`: the question maps to one explicit knowledge scope and confirmed/current facts cover it. The answer is composed from those facts and carries provenance citations.
- `MISSING_KNOWLEDGE`: the question has a valid scope but no confirmed/current fact covers it. Runtime must refuse or hand off.
- `COVERAGE_GAP`: the question cannot be mapped deterministically to one knowledge scope. Runtime must not guess the intended scope.

## Safety invariants
1. Unconfirmed proposals are never answerable.
2. Superseded facts are never answerable by default.
3. Workspace boundaries are preserved.
4. Answers preserve source span and extraction provenance.
5. The answer layer creates no facts and performs no mutations.
6. The answer layer creates no authorization or execution authority.
7. An LLM may later be placed behind this context boundary, but it must receive only governed grounded context and may not fill a missing-knowledge state.

## Deterministic scope inference
Question-to-knowledge scope inference is intentionally conservative. A question must match exactly one supported semantic scope. Ambiguous questions return `COVERAGE_GAP` rather than selecting a scope arbitrarily.

## Example
Question: `What are your opening hours?`

If confirmed hours exist:
- status: `GROUNDED`
- answer: rendered from the confirmed hours fact
- citation: exact operator source span + extraction ID

If no confirmed hours exist:
- status: `MISSING_KNOWLEDGE`
- answer: null
- handoff/refusal: required

## Endpoint
`POST /api/v1/business-activation/agent/answer`

Request:
```json
{
  "workspace_id": "ws-1",
  "question": "What are your opening hours?"
}
```

Optional explicit scope:
```json
{
  "workspace_id": "ws-1",
  "question": "Tell me something about the business.",
  "fact_type": "business_hours"
}
```

## Non-goals
- No autonomous action execution.
- No second knowledge store.
- No model-based hallucination fallback.
- No mutation of Knowledge Plane during answer generation.
- No claim of live production deployment.
