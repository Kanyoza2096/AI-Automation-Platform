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
