# U2 retrieval relevance calibration audit — development split only

**Status:** proposed development judgments approved by the user on 2026-11-08; source benchmark unchanged  
**Scope:** five development queries inspected against the corpus record bodies  
**Holdout policy:** no holdout query, label, or ranking result was used to propose calibration changes. In particular, KDO-RET-001 remains sealed.

## Why this audit exists

The original benchmark assigns grade 0 / `hard_negative` to records that are sometimes substantively relevant but less specific than the designated gold record. A less-specific relevant passage is not an irrelevant passage. Treating it as grade 0 can reward a retriever for suppressing useful evidence and can make hard-negative metrics misleading.

The judgments below were approved by the user on 2026-11-08. They are stored as a separate, versioned development-only overlay at `docs/retrieval/calibration/kdo_retrieval_dev_calibration_v1.json`; the original benchmark remains unchanged. The overlay changes only the listed query-record grades and does not authorize production ranking changes.

## Proposed development-set judgments

Grades: 3 = highly relevant / direct answer; 2 = relevant supporting evidence; 1 = marginal context; 0 = not relevant to this query's information need.

| Query | Record | Existing grade | Proposed grade | Evidence and rationale |
|---|---|---:|---:|---|
| KDO-RET-002 | `knowledge.ai-tool-schema-design.v1` | 0 | 2 | Explicitly says schema validation catches malformed arguments. It is relevant supporting evidence, although the agent-evaluation record is the direct answer about what to measure. |
| KDO-RET-014 | `knowledge.ai.llm-judge-calibration.v1` | 0 | 2 | Explains evaluator bias, score error, judge drift, and why scores can improve without real quality improving. |
| KDO-RET-014 | `knowledge.ai.eval-dataset-design.v1` | 0 | 2 | Explains coverage gaps and aggregate metrics hiding regressions; relevant to a score/user-correction discrepancy. |
| KDO-RET-014 | `knowledge.ai-rag-evaluation.v1` | Not labelled | 1 | Broad evaluation/error-isolation context, but less directly about user correction rates than online evaluation and judge calibration. |
| KDO-RET-020 | `knowledge.dsa.range-query-structures.v1` | 0 | 2 | Its content explicitly describes segment trees and says lazy propagation supports some range updates. It is less detailed than the dedicated lazy-segment-tree record, but not irrelevant. |
| KDO-RET-024 | `knowledge.dsa.hash-tables.v1` | 0 | 2 | Explicitly discusses collision costs, expected O(1), and degradation under collisions. It is directly relevant supporting evidence, though less detailed than the collision-specific gold record. |
| KDO-RET-044 | `knowledge.hld.resilience.v1` | 0 | 2 | Explicitly covers retries multiplying load during overload, deadlines/timeouts, and bulkheads. It is relevant supporting evidence, though the gold record adds load shedding and retry budgets. |

No proposed change is made to the gold labels. KDO-RET-014's two current gold records remain highly relevant: online evaluation directly addresses user correction rate; failure taxonomy supports classifying the cause of metric disagreement.

## Controlled ranking experiment (development split only)

A local, deterministic BM25 candidate was compared with the current TF-IDF/cosine implementation using the same content-only corpus representation, tokenizer, and development queries. No code, production retriever, or benchmark labels were changed.

| Development metric | Current TF-IDF | BM25 candidate |
|---|---:|---:|
| True Recall@5 | 0.9400 | 0.9000 |
| Gold hit rate@5 | 0.9600 | 0.9200 |
| MRR | 0.8280 | 0.8400 |

Interpretation:
- BM25 improves MRR slightly but loses recall and query-level gold coverage.
- The current hard-negative top-1 metric is not a sound selection criterion until the relevance labels above are reviewed; several supposed negatives are relevant.
- This is one baseline comparison, not a parameter search. No candidate is selected.
- No holdout result was calculated or consulted for this experiment.

### Additional candidate: Porter stemming (research-only)

A second local candidate applied the NLTK Porter stemmer to both query and content tokens before using the existing TF-IDF/cosine scoring formula. This was an offline experiment only; NLTK is not assumed to be an approved production dependency, and no runtime tokenizer was changed.

| Development metric | Current TF-IDF | BM25 candidate | Porter-stemmed TF-IDF candidate |
|---|---:|---:|---:|
| True Recall@5 | 0.9400 | 0.9000 | 0.9400 |
| Gold hit rate@5 | 0.9600 | 0.9200 | 0.9600 |
| MRR | 0.8280 | 0.8400 | 0.8680 |

The stemmed candidate improves development MRR while preserving the baseline recall and gold-hit rate. However, it still does not retrieve either KDO-RET-014 gold record in the top five. It also changes rankings in other queries, so this is a candidate for a carefully controlled follow-up, not a selected implementation. No dependency, code, or production behavior was changed.

## Per-query observations

| Query | Current TF-IDF top results | BM25 top results | Initial interpretation |
|---|---|---|---|
| KDO-RET-002 | agent workflows, agent evaluation, orchestration, schema design | agent evaluation, agent workflows, orchestration, schema design | BM25 puts the direct evaluation record first; schema design remains useful supporting evidence. |
| KDO-RET-014 | RAG evaluation, judge calibration, agent evaluation, dataset design | agent evaluation, RAG evaluation, retrieval evaluation, dataset design, judge calibration | Neither puts online evaluation in the top two; the exact user-correction wording is not matched well. BM25 retrieves one gold at rank 3. |
| KDO-RET-020 | general range-query structures, lazy segment tree | same two in same order | The general range-query record is relevant and should not be penalized as an irrelevant negative. |
| KDO-RET-024 | general hash tables, collision-specific hashing | same two in same order | The general hash-table record is relevant, not a hard negative. |
| KDO-RET-044 | dedicated deadlines/load-shedding record, general resilience | same two in same order | Both are relevant; the dedicated record is correctly ranked first. |

## Recommended next actions

1. **Completed:** user approved the development relevance judgments; the versioned overlay was created without modifying the source benchmark.
2. Recompute development metrics with the overlay, including nDCG@5, true Recall@5, and MRR. This requires preserving/exporting the per-query ranked result lists for each candidate; aggregate-only metrics are insufficient to calculate graded nDCG retrospectively.
3. Investigate query formulation / corpus wording for KDO-RET-014 before adding query expansion or changing runtime ranking.
4. Compare a small number of predeclared ranking candidates on development only. Freeze one candidate before a single independent holdout measurement.

## Safety and scope

- No Supabase project was queried or modified.
- No production workspace, credentials, database, Render service, publishing path, or external account was accessed.
- No runtime ranking code, scoring weights, thresholds, or source benchmark labels were changed. The approved grade overrides exist only in the separate development calibration overlay.
- No holdout data was used to tune or select the BM25 candidate.


## Graded relevance diagnostic — five calibrated development queries

After approval, nDCG@5 was calculated for the five queries covered by the overlay, using gains of `2^grade - 1` and the explicitly judged pool (gold IDs, original hard-negative IDs, and approved overlay judgments). Unjudged retrieved records were assigned grade 0, so this is a limited judged-pool diagnostic rather than a complete relevance assessment.

| Candidate | Mean nDCG@5 across five calibrated queries |
|---|---:|
| Current TF-IDF | 0.7193 |
| BM25 (k1=1.5, b=0.75) | 0.7796 |
| Porter-stemmed TF-IDF | 0.7507 |

The per-query ranked IDs and scores are recorded in `docs/retrieval/calibration/kdo_u2_calibrated_dev_ndcg_snapshot_v1.json`.

Important interpretation:
- This is only five of the 25 development queries; it is not a full development benchmark result.
- BM25 has the highest mean on this small slice, but performs poorly on KDO-RET-014 (nDCG@5 0.1318), and the earlier full-development comparison showed lower Recall@5 and gold-hit rate than baseline.
- Porter-stemmed TF-IDF scores 0.2777 on KDO-RET-014 and is not selected.
- No candidate is selected or promoted. KDO-RET-014 remains the priority for query/corpus diagnosis.
- No holdout data was read, scored, or used.
