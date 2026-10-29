# R11 — Business Activation Runtime Knowledge Read

## Boundary
R11 completes the next Business Activation boundary after generalized knowledge ingestion and durable confirmation.

The runtime read path consumes only the existing Business Activation Knowledge Plane. It does not create a second knowledge store, answer engine, authorization system, or execution path.

## Contract
`POST /api/v1/business-activation/knowledge/read`

Request:
- `workspace_id`
- optional `fact_type` for a narrow category read
- `business_knowledge` as the deliberate generalized wildcard over the existing supported categories
- optional `subject`, `predicate`, `conditions`, `limit`

Response states:
- `COVERED` — confirmed current knowledge exists for the requested scope; runtime may use the returned facts and citations.
- `MISSING_KNOWLEDGE` — no confirmed current fact covers the scope; runtime must refuse or hand off rather than infer.
- `COVERAGE_GAP` — no explicit knowledge scope was supplied; runtime must not guess what knowledge is relevant.

Each covered result carries:
- fact identity and category
- predicate/source
- exact `source_span`
- `extraction_id`
- confirmation timestamp

## Safety invariants
1. Unconfirmed proposals never appear in runtime reads.
2. Superseded facts never appear in default runtime reads.
3. Workspace scope is enforced before the read.
4. `business_knowledge` is a controlled wildcard over the same Knowledge Plane, not a second store.
5. Missing knowledge is explicit and actionable; absence is never converted into an answer.
6. The read path does not authorize, execute, mutate, or bypass the Action Kernel.

## Command Center behavior
After explicit confirmation, Business Activation reads the confirmed fact categories back through the canonical backend endpoint and displays the runtime status plus provenance. Operators can refresh the read path without re-teaching the business.

## Evidence
Selected cumulative regression: **41/41 PASS**.
Python compileall: **PASS**.
Full frontend TypeScript/build: **NOT CLAIMED** because `node_modules` is absent.
Live PostgreSQL/Supabase deployment: **NOT CLAIMED**.
Physical mobile-browser validation: **NOT CLAIMED**.
