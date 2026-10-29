# R9 — Business Activation Knowledge Storage & Versioning

## Boundary

Phase 5 converts the reviewed Business Activation proposal into durable Knowledge Plane state. Command Center remains the operator/projector surface; it does not become the fact authority.

```text
operator teaching
  → extraction proposal
  → review / correction
  → explicit confirmation
  → Knowledge Plane fact version
  → supersession
  → knowledge_updated
  → confirmed-only runtime read
```

## Exactly two v1 fact types

- `business_hours`
- `refund_policy`

## Persistence rules

- Extraction proposals are audited in `business_activation_extractions`.
- Proposed facts are stored as `confirmed=false`; they are not runtime-readable.
- Confirmation is explicit and backend-authoritative.
- A new confirmed version is inserted; the previous active version is superseded.
- A partial unique index guarantees one active confirmed fact per workspace/type/subject/predicate/conditions.
- Repeating the same `extraction_id` is idempotent and does not create another version or emit another update event.
- `knowledge_updated` is published through the existing `UnifiedEventBus` with workspace scope and correlation context.
- The current-facts read path returns only `confirmed=true` and, by default, `superseded_at IS NULL`.

## Security

The API requires canonical authentication and authenticated workspace matching. Browser clients do not receive direct Data API write authority. The migration enables RLS and the backend uses its existing service-side persistence boundary.

## Live database gate

Migration `136_business_activation_knowledge_confirmation.sql` must be applied to the target PostgreSQL/Supabase database before durable production persistence is considered live. No live database deployment is claimed by this artifact.

## Validation

- Business Activation contract tests: 7/7 PASS
- Phase 5 knowledge tests: 6/6 PASS
- Cumulative runtime/security regression selected suite: 24/24 PASS
- Changed frontend TSX/TS transpilation syntax validation: PASS
- Full dependency-backed frontend typecheck/build: not claimed because the cumulative archive does not include `node_modules`.
