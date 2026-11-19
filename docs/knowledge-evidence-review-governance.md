# Knowledge evidence review governance

## Purpose

Knowledge passages are useful context, but they are not automatically evidence for empirical or quantitative claims. This workflow provides an autonomous, auditable path for establishing source provenance without silently trusting imported or historical knowledge. Human review is reserved for quarantined exceptions, not routine approval.

## Review case requirements

A review case snapshots:
- the exact knowledge entry ID and SHA-256 of its content;
- the source ID, URL, title, source type, and trust class available at submission time;
- the entry metadata snapshot and intended claim scope;
- the reviewer identity, decision time, and review notes.

A content edit changes the content hash and therefore requires a new review case. An approval must identify a reviewer and timestamp and include a stable source ID and source URL.

## Decision policy

- `pending`: awaiting source inspection.
- `approved`: KDO has independently checked the captured source and confirmed the exact entry is traceable to it.
- `rejected`: the source is missing, inaccessible, mismatched, unreliable, or insufficient for the proposed claim scope.
- `superseded`: the reviewed content has changed or a newer review case replaces it.

Every decision is recorded in the append-only `knowledge_evidence_review_events` table. The tables are service-role-only; browser clients and anonymous/authenticated roles receive no direct table privileges.

## Important separation

Creating or approving a review case does **not** mutate `knowledge_entries.metadata.verified`. Promotion into the content evidence gate must be a separate, explicit, audited operation that rechecks the current entry content hash and the reviewed source. Until that operation exists and is approved, the existing evidence gate remains authoritative and empirical claims remain blocked without `metadata.verified=true`.

## Initial production triage

The current affected brands fall into distinct review cohorts:
- GrowthBridge Virtual: published website service descriptions and a user-approved brand-direction record. These can support bounded brand/service descriptions only after their source snapshots and current website state are checked; they do not prove customer outcomes.
- Ships & Shrugs: handbook-derived technical explanations with source URLs and attribution notes. Each source curriculum, exact claim, and license/attribution conditions must be checked before approval.
- Third brand: 732 records with no source type, trust class, source ID, or source URL metadata. These require source recovery or rejection; entry IDs alone are not proof of provenance.

No record should be batch-approved based only on a URL being present, a generated attribution string, or a matching topic.


## API lifecycle

The authenticated API exposes:
- POST /api/v1/brands/{brand_id}/knowledge/ingest-source to fetch a public URL server-side, capture its text, attach a server-generated HMAC attestation, and immediately run autonomous review. The hostname must be explicitly allowlisted by an operator.
- POST /api/v1/brands/{brand_id}/knowledge/{knowledge_id}/evidence-review to submit an exact content version. Repeated submissions reuse the same case; rejected/superseded cases can be reopened.
- GET /api/v1/brands/{brand_id}/knowledge-evidence-reviews to list brand-scoped cases by status.
- POST /api/v1/brands/{brand_id}/knowledge-evidence-reviews/{case_id}/decision for a human reviewer to approve or reject a pending case.

All endpoints require workspace write access and use the service-role client only on the server. Autonomous decisions are recorded under the KDO service actor. The exception decision endpoint remains human-governed. The API rechecks the entry content hash before decision, and the database guard locks the knowledge row and rechecks the hash and tenant binding in the same transaction. Approved snapshots are immutable; a later content edit cannot inherit the approval.

Approval is reusable source governance, not per-post moderation. It does not promote evidence into the publishing validator; evidence_promoted remains false until a separate audited promotion operation is implemented and reviewed.


## Separate publishing-gate promotion

A separate human-authorized endpoint promotes an approved case into the existing deterministic evidence gate. It invokes the database RPC promote_approved_knowledge_evidence, which locks the review case and knowledge row, rechecks the content hash and tenant binding, writes the verified source metadata, and records an append-only promotion event in the same transaction. Repeated promotion calls are idempotent.

If the knowledge content is later edited, a database trigger automatically sets the promoted entry's verified flag to false, marks the evidence stale, supersedes the old review case, and appends a revocation event. A new content version therefore needs a new review and promotion. No existing records are bulk-promoted.


## Autonomous KDO review and promotion

Routine evidence review is autonomous; an operator is not expected to approve each
case. New single and bulk knowledge submissions automatically enter the review
queue. When a supplied source URL belongs to an exact-host allowlist, the backend
fetches it, signs the captured text, and reviews it immediately. Entries without
valid captured provenance are still reviewed but quarantined. The KDO reviewer
makes one of three decisions:

- `approved`: every material claim is directly supported by the captured source,
  the source identity and scope match, claim coverage is at least 0.98, and
  independently assessed confidence is at least 0.97. KDO then invokes the
  existing transactional promotion RPC.
- `rejected`: clear contradiction, fabricated attribution, or a demonstrably
  unrelated/unsafe source.
- `quarantined`: missing or ambiguous provenance, uncertain claims, malformed
  model output, unavailable reviewer, or any failed deterministic gate.

A URL, title, source ID, source excerpt, or model confidence score is never
sufficient by itself. The trusted ingestion boundary must capture the actual
source text and attach an HMAC attestation using the server-only
`KDO_KB_SOURCE_SNAPSHOT_HMAC_SECRET`. The attestation binds workspace, source
ID, URL, trust class, and SHA-256 of the exact captured text. Use
`services.autonomous_knowledge_evidence_reviewer.build_source_snapshot_attestation`
only inside trusted server-side ingestion code; never expose the secret to a
browser or accept a client-provided signature.

The unattended sweep is available as
`python -m scripts.autonomous_knowledge_evidence_review`. It processes a bounded
batch per run (default 100 candidates, configurable with
`KDO_KB_REVIEW_BATCH_SIZE`), creates idempotent review cases for existing
workspace/brand knowledge entries, reviews pending cases, retries idempotent
promotion for approved cases, and reopens quarantined cases only when a captured
source snapshot becomes available. The sweep uses the durable cursor table introduced in migration 186 so bounded runs advance through the corpus rather than repeatedly scanning only the first page. For historical entries with an exact-host allowlisted source URL, the sweep fetches the source server-side, verifies/rebuilds the HMAC snapshot attestation, and then reviews the entry. Already-approved entries are not refetched. Entries without valid provenance are quarantined. Schedule it as a Render Cron Job only after migrations 185–187 and trusted-ingestion attestation are deployed. Do not run it against
an unconfirmed database project.

No historical entries are bulk-approved by migration. Existing records without
a valid signed source snapshot are automatically quarantined, not silently
trusted. This is intentional: KDO reviews everything, but it only approves
evidence it can actually verify.


## Trusted source configuration

The server-side ingestion route requires two backend-only environment variables:

- `KDO_KB_SOURCE_SNAPSHOT_HMAC_SECRET`: a high-entropy secret used to bind the captured text to its tenant and source identity.
- `KDO_KB_TRUSTED_SOURCE_DOMAINS`: a JSON object mapping exact hostnames to one allowed trust class. Example shape (configure only hosts actually vetted by the operator): `{"who.int":"government","docs.example.org":"official_documentation"}`.

Subdomains do not inherit trust. A source is fetched through the existing web-intelligence gateway, with private/local network destinations and redirects rejected. Missing or invalid configuration fails closed. The trust registry must be curated; do not add a domain merely because it appears in a submitted URL.


Migration 187 limits the service-role grants on review cases and sweep state to the read/insert/update operations used by the backend; it does not need delete or truncate authority.
