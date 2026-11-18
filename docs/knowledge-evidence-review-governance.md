# Knowledge evidence review governance

## Purpose

Knowledge passages are useful context, but they are not automatically evidence for empirical or quantitative claims. This workflow provides a human-reviewed, auditable path for establishing source provenance without silently trusting imported or historical knowledge.

## Review case requirements

A review case snapshots:
- the exact knowledge entry ID and SHA-256 of its content;
- the source ID, URL, title, source type, and trust class available at submission time;
- the entry metadata snapshot and intended claim scope;
- the reviewer identity, decision time, and review notes.

A content edit changes the content hash and therefore requires a new review case. An approval must identify a reviewer and timestamp and include a stable source ID and source URL.

## Decision policy

- `pending`: awaiting source inspection.
- `approved`: a human reviewer has checked the source and confirmed the exact entry is traceable to it.
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
- POST /api/v1/brands/{brand_id}/knowledge/{knowledge_id}/evidence-review to submit an exact content version. Repeated submissions reuse the same case; rejected/superseded cases can be reopened.
- GET /api/v1/brands/{brand_id}/knowledge-evidence-reviews to list brand-scoped cases by status.
- POST /api/v1/brands/{brand_id}/knowledge-evidence-reviews/{case_id}/decision for a human reviewer to approve or reject a pending case.

All endpoints require workspace write access and use the service-role client only on the server. Decisions are restricted to the authenticated human identity. Approval requires a stable source ID and HTTP(S) source URL. The API rechecks the entry content hash before decision, and the database guard locks the knowledge row and rechecks the hash and tenant binding in the same transaction. Approved snapshots are immutable; a later content edit cannot inherit the approval.

Approval is reusable source governance, not per-post moderation. It does not promote evidence into the publishing validator; evidence_promoted remains false until a separate audited promotion operation is implemented and reviewed.


## Separate publishing-gate promotion

A separate human-authorized endpoint promotes an approved case into the existing deterministic evidence gate. It invokes the database RPC promote_approved_knowledge_evidence, which locks the review case and knowledge row, rechecks the content hash and tenant binding, writes the verified source metadata, and records an append-only promotion event in the same transaction. Repeated promotion calls are idempotent.

If the knowledge content is later edited, a database trigger automatically sets the promoted entry's verified flag to false, marks the evidence stale, supersedes the old review case, and appends a revocation event. A new content version therefore needs a new review and promotion. No existing records are bulk-promoted.
