# Business Activation — Phase 0–3 Foundation

## Scope

This increment implements the foundation and extraction contract only:

- Phase 0: canonical frontend read-error diagnostics and explicit backend-unavailable state handling.
- Phase 1: exactly two v1 fact types: `business_hours` and `refund_policy`.
- Phase 2: typed fact envelope plus extraction/clarification contracts.
- Phase 3: authenticated, workspace-scoped extraction endpoint.

It does **not** confirm facts, write confirmed facts, expose facts to agents, or create a second knowledge authority. Those boundaries remain future phases.

## Extraction endpoint

`POST /api/v1/business-activation/extractions`

Input:

```json
{
  "workspace_id": "workspace-id",
  "operator_text": "We are open Monday-Friday 9:00-17:00 EAT. Refunds are accepted within 14 days if the item is unused.",
  "fact_types_in_scope": ["business_hours", "refund_policy"]
}
```

Output contains:

- stable `extraction_id` derived from workspace + input + scope
- `proposed_facts`
- `clarifications`
- `unparsed_text`
- `extraction_confidence`
- `model_used`
- `extracted_at`
- `input_fingerprint`

The same input and scope produces the same extraction identity. No values are invented to satisfy missing fields.

## Safety boundaries

- Authentication is required.
- The requested workspace must equal the authenticated workspace.
- Proposed facts are `confirmed=false`.
- Facts below 0.85 confidence are not proposed; they become clarification requests.
- The extractor performs no storage mutation and no external action.
- Supabase tables are RLS-enabled and intentionally not granted as a browser-facing Data API authority; KCPG/backend remains the application boundary.

## Phase 0 caveat

The shared diagnostic surface is implemented, but a live Network-tab diagnosis of the existing Situation/Business Activation read failure cannot be claimed from this archive alone. Production verification still requires an authenticated environment with the failing request available.
