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

## Initial production triage (read-only inventory, 2026-11-20)

The production review-case inventory contains 854 quarantined cases, with no verified knowledge entries, no approved review cases, and no promotion events at the time of inspection. The cohorts are:

- **GrowthBridge Virtual — 10 cases:** 8 published website-context cases, 1 user-approved brand-direction case, and 1 authenticated website-context snapshot. All 10 lack a source URL. The website-context records carry an untrusted-business-context trust label; the user-approved brand-direction record is not a substitute for a captured public source. These may support bounded brand/service descriptions only after source identity and current context are verified; they do not prove customer outcomes.
- **Ships & Shrugs — 112 technical knowledge chunks:** 60 HLD handbook entries, 31 AI Engineering Handbook entries, and 21 DSA handbook entries. Source URLs are present across the three handbook domains, but all 112 review cases lack an approved trust class and signed source snapshot. A repository-level provenance lead is now verified: all three domains (HLD, DSA, and AI Engineering) are published from `handbook-academy/engineering-handbook`; its README and contribution guide identify the three curricula, its `CITATION.cff` names the project author, and its repository LICENSE is CC BY-SA 4.0. This establishes a credible publisher/repository and license lead, not a completed match for each imported chunk. Each of the 112 records still needs exact chapter/content mapping, confirmation that the copied passage is within the licensed content (rather than separately sourced third-party material), and compliant attribution/ShareAlike treatment before it can be approved.
- **New Nature in Christ Community — 732 scripture reflections:** the entry metadata contains `source=spurgeon_morneve`, scripture reference, date, theme, and time-of-day fields. A matching source-identity lead is now verified in `OpenChristianData/open-christian-data`: its source note identifies *Morning and Evening: Daily Readings*, the retained CCEL ThML electronic witness, the CCEL source URL, and the exact historical source/edition identifiers. Its October 13, 2026 data notice states a bounded United States public-domain disposition for the selected underlying historical devotional text, while excluding CCEL's electronic file, formatting, and modern material; it also requires the source credit “sourced via CCEL.org.” The repository's source manifest binds the retained witness by SHA-256. This is materially stronger provenance than the prior metadata label, but the production rows have not yet been reconciled against the exact source witness and selected scope. Production review cases still have null source URLs, generated `unverified-entry:<id>` source IDs, no trust class, and no signed source snapshots. Do not bulk-approve or promote until row-level content/date/time mapping and the exact selected-text boundary are verified, then capture the canonical source and rights notice in KDO's trusted snapshot workflow.

No record should be batch-approved based only on a URL being present, a generated attribution string, a source label, or a matching topic. These counts describe the review-case snapshot at the time of inspection and must be refreshed before any operational decision.

## Ships & Shrugs handbook row-level provenance assessment (read-only, 2026-11-20)

The 112 production records have been inspected at metadata and sample-content level. All 112 have a `chapter_title` and a source URL, but the URLs are not uniformly chapter-specific:
- 60 records point to a chapter-level curriculum URL.
- 52 point only to a handbook/curriculum root or broad section URL.
- 78 records describe themselves as “original explanatory synthesis” and explicitly “not a verbatim reproduction” (AI Engineering 27, DSA 20, HLD 31).
- 34 describe themselves as paraphrased/derived from handbook material (AI Engineering 4, DSA 1, HLD 29); all 34 have chapter-level source URLs.
- A `source_word_count` is populated on only 38 records, so it cannot be treated as a reliable completeness or similarity measure for the cohort.

The public publisher repository confirms that the three handbooks are in `handbook-academy/engineering-handbook` and the repository declares CC BY-SA 4.0. The repo README identifies the HLD, DSA, and AI Engineering websites and states that the books' source text lives in that repository. This verifies publisher/repository identity and the repository's declared license, but not that each production synthesis is a derivative work or that each source claim is accurately supported by the cited chapter.

Disposition: keep all 112 quarantined pending row-level source review. For the 34 explicitly paraphrased rows, verify the cited chapter and check attribution/ShareAlike compliance. For the 78 original-synthesis rows, do not label them CC BY-SA adaptations solely because they share a topic or curriculum URL; verify whether the entry is independently authored or derived from specific chapter expression, and establish evidence support for its factual/technical claims. For the 52 broad-URL records, identify the exact source chapter before snapshot capture. Check for third-party figures, quotations, code, and externally sourced claims inside handbook chapters; the repository-wide license alone does not relicense third-party material.

No production rows, review cases, trust classes, source snapshots, or promotion states were changed. This was a read-only metadata and sample-content inspection.

## GrowthBridge website source recovery (read-only, 2026-11-20)

A production configuration record now supplies the canonical site origin for the GrowthBridge integration: `https://growthbridgevirtual.onrender.com` (`website_integrations.integration_id=growthbridge-production`, environment `production`, status `configured`). The integration record exposes the base URL and event/capability declarations, but does not establish the public route pattern for individual service pages. Do not construct service URLs from slugs until the website's actual route contract is verified.

The eight published-service context records have stable service source IDs and slugs, with a shared source-updated timestamp of 2026-09-10:
- `data-analytics-reporting`
- `brand-identity-positioning`
- `business-consulting-growth-advisory`
- `digital-marketing-strategy`
- `paid-advertising-performance-marketing`
- `seo-content-marketing`
- `social-media-management`
- `web-design-development`

The authenticated website context snapshot was fetched 2026-11-14 but reports source-updated time 2026-09-02; it is explicitly stale-review-required and untrusted business context. Its captured content includes organization/SEO fields and the eight service records, but not canonical page URLs. The four integration events in the table are KDO smoke-test events (one explicitly marks `synthetic=true)); they are not evidence that the service pages were published or that their content matches the quarantined knowledge chunks.

The tenth GrowthBridge case is a user-approved brand-direction record from conversation dated 2026-11-17. It may inform approved brand configuration, but it is explicitly not verified as a website fact and must not be converted into public-source evidence. The ninth case is the stale authenticated website-context snapshot.

Disposition: the production site origin and eight service slugs are now recovered, but exact page URLs and current page contents remain unverified. Refresh the website context through the governed connector and capture page-level source URLs/content hashes before promotion. Do not use the smoke-test events as source evidence. No secrets, credentials, production data, or review decisions were changed.

## External provenance research (read-only, 2026-11-20)
## Spurgeon production-to-source reconciliation (read-only, 2026-11-20)

The 732 production entries were compared against all 732 records in the identified OpenChristianData source JSON. Matching by production date and time-of-day maps every production row to one unique source entry (732/732). This is a row-level identity reconciliation, not a rights approval or evidence promotion.

Content comparison after Unicode normalization, case folding, whitespace normalization, and the known source/production hyphenation normalization yielded:
- 671/732 exact content matches.
- 43/732 differ only by one hyphenation character.
- 11/732 contain an appended next-month calendar list after otherwise matching devotional text.
- 7/732 require manual content/reference review. These are primarily citation/formatting discrepancies: two production rows contain additional scripture references absent from the source, one row adds/repeats a verse heading, and several rows need poem/line-break or hyphenation alignment. The November 3 morning row needs manual alignment of the opening quotation and closing poem, but should not be labelled a substantive text mismatch without further review.
- Scripture metadata matches the source reference on 690/732 rows. The 42 mismatches comprise six missing scripture values and 36 abbreviated `Solomon` references where the source uses `Song of Solomon`.

The manual-review queue is explicitly enumerated:
- Appended calendar text (11): `kb-0d0673d5d0ad`, `kb-269ebb5ce789`, `kb-31132fdd1e56`, `kb-46c246c34e1e`, `kb-571e158131ab`, `kb-59d921bdf421`, `kb-84de63479eac`, `kb-90c7c1f9f28f`, `kb-af8e80dd161f`, `kb-dace93cf124a`, `kb-fa1b79587228`.
- Other content/reference review (7): `kb-36e7f6fb7cf8`, `kb-78c20f498711`, `kb-911c707a0001`, `kb-9a7e0c28bae8`, `kb-9e8a2a6710ed`, `kb-b95564907ae8`, `kb-bb22c9c5a1af`.
- The 43 single-hyphen differences are typographic/hyphenation variants, not approved edits; retain source comparison evidence and do not rewrite production text without a governed correction.
The source record identity is therefore established across the cohort, but the content anomalies must be reviewed and corrected or explicitly dispositioned before promotion. The 11 calendar appendices and 7 manual-review cases must not be silently normalized away. The six missing references and 36 Song-of-Solomon aliases can be reconciled deterministically against the source record, subject to normal review controls.

No production rows, metadata, review cases, trust classes, source URLs, or promotion states were changed. No bulk approval was performed. The comparison used the public source collection and a read-only production export.

- Spurgeon source lead: [source note](https://github.com/OpenChristianData/open-christian-data/blob/main/sources/devotionals/spurgeons-morning-evening/README.source.md), [selected-data rights and credit notice](https://github.com/OpenChristianData/open-christian-data/blob/main/data/devotionals/spurgeons-morning-evening/README.md), and [source manifest](https://github.com/OpenChristianData/open-christian-data/blob/main/sources/devotionals/spurgeons-morning-evening/lib030.source-manifest-current-closure-20261003-v1.json). The source note identifies the exact CCEL ThML witness and distinguishes historical devotional text from the electronic file/formatting. The selected public scope is qualified to the United States; no worldwide rights conclusion is made. This does not yet prove that every production row is an exact match to that selected scope.
- Handbook publisher/license lead: [engineering-handbook repository](https://github.com/handbook-academy/engineering-handbook), [repository license](https://github.com/handbook-academy/engineering-handbook/blob/main/LICENSE), [contribution guide](https://github.com/handbook-academy/engineering-handbook/blob/main/CONTRIBUTING.md), and [citation metadata](https://github.com/handbook-academy/engineering-handbook/blob/main/CITATION.cff). The repository documents all three handbook families and CC BY-SA 4.0, but each imported chunk still needs source-chapter mapping and a check for third-party material and required attribution/ShareAlike terms.
- No production database rows were changed, approved, or promoted during this research. No trusted-domain allowlist or secrets were changed.


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


## Scheduled backlog processing

The FastAPI application lifespan starts the bounded historical sweep automatically after the full boot sequence when
both `KDO_KB_SOURCE_SNAPSHOT_HMAC_SECRET` and
`KDO_KB_TRUSTED_SOURCE_DOMAINS` are configured. It runs on the existing
single web instance in a background thread, advances the durable database cursor
once per interval, and stops after reaching the end of the corpus with no pending
cases. The default interval is one hour (bounded by
`KDO_KB_REVIEW_SWEEP_INTERVAL_SECONDS`). This avoids a separate paid worker or
cron service.

An optional internal trigger is available at
`POST /api/v1/internal/knowledge-evidence-review/sweep`. It requires the
server-only `X-KDO-Review-Sweep-Token` header and acknowledges an active sweep
without starting duplicate work. A scheduler calling this hook needs only the
backend URL and token, not Supabase or LLM credentials.


The scheduler emits structured completion/failure logs and stops itself after a full clean pass.

## Read-only candidate backlog

The brand-scoped endpoint
`GET /api/v1/brands/{brand_id}/knowledge-evidence-backlog`
provides a paginated view of entries whose `metadata.verified` value is not
explicitly true. It computes the current content hash, shows a bounded content
preview, distinguishes explicitly unverified from verification-unspecified
records, and reports the latest matching review state. If the content hash no
longer matches the latest case, the candidate is surfaced as superseded rather
than inheriting an earlier decision.

The endpoint is read-only. It does not create review cases, change knowledge
metadata, approve sources, or invoke promotion. A reviewer opens the full
knowledge entry, submits that exact version to the existing review API, records
a human decision, and invokes promotion separately only after approval.
