# U2 offline query-understanding prototype — development audit

**Status:** exploratory only; not approved for runtime use.

## Scope and safety

- Evaluated all 25 development queries only. The 26-query holdout was not opened or evaluated.
- Used the 112 knowledge-chunk records from corpus version 6.0 and the existing TF-IDF/inverted-index evaluator representation.
- The prototype preserves the original query, records intent/concept terms, and appends a small fixed set of general-language aliases.
- Expansion vocabulary was authored independently of corpus text, gold IDs, relevance labels, and target record IDs.
- No LLM calls, database access, writes, publishing, deployment, or runtime integration occurred.

## Full development results

| Metric | Baseline | Prototype |
|---|---:|---:|
| Recall@5 (fraction of gold records retrieved) | 0.9400 | 0.9400 |
| Gold hit rate@5 (queries with >=1 gold) | 0.9600 | 0.9600 |
| MRR | 0.8280 | 0.8480 |
| Binary nDCG@5 | 0.8381 | 0.8528 |
| Hard-negative top-1 rate | 0.2000 | 0.1600 |
| Hard-negative outrank rate | 0.3333 (6/18) | 0.2778 (5/18) |

Binary nDCG is used here because the approved graded overlay covers only five development queries. It must not be compared directly with the five-query graded nDCG experiment.

## Interpretation

The prototype produces a modest ranking improvement without improving recall or query-level gold coverage. KDO-RET-002 moves its gold result from rank 2 to rank 1. No other gold-rank change was observed. KDO-RET-014 remains unresolved: neither gold record appears in the top five.

This is evidence that generic alias expansion may help in a narrow case, but it is not evidence that a generic synonym dictionary is a sufficient query-understanding system. It is not selected or promoted.

## Latency

Local microbenchmarks were not controlled well enough to support a meaningful latency comparison. Relation decomposition also adds retrieval calls. No hosted latency or cost claim is made.



## Second experiment: relation-aware clause decomposition

The next offline prototype adds shallow extraction of contrast, comparison, and causal connectors. It preserves the complete original query and creates clause-level subqueries. The original query receives RRF weight 1.0; each extracted clause receives weight 1.5; reciprocal-rank fusion uses (k=10) and each subquery contributes up to 20 candidates.

| Metric | Baseline | Generic aliases | Relation-aware RRF (clause weight 1.5) |
|---|---:|---:|---:|
| Recall@5 | 0.9400 | 0.9400 | **0.9600** |
| Gold hit rate@5 | 0.9600 | 0.9600 | **1.0000** |
| MRR | 0.8280 | 0.8480 | **0.8560** |
| Binary nDCG@5 | 0.8381 | 0.8528 | **0.8623** |
| Hard-negative top-1 rate | 0.2000 | **0.1600** | **0.1600** |
| Hard-negative outrank rate | 0.3333 (6/18) | **0.2778 (5/18)** | 0.3158 (6/19) |

The relation-aware candidate improves every aggregate metric relative to the baseline on this development slice. Compared with generic aliases, it improves recall, gold hit rate, MRR, and binary nDCG, while its hard-negative outrank rate is slightly worse.

### Important diagnostic: KDO-RET-014

The parser identifies the contrast connector “even though” and creates two clauses:
- “Why can my model evaluation score improve”
- “users are correcting the answers more often”

The second clause retrieves `knowledge.ai.online-evaluation.v1` at rank 1 in its own list. Fusion moves that gold record into the combined top five at rank 5. The other gold record, `knowledge.ai-failure-taxonomy.v1`, remains outside the top five, and a hard negative remains at rank 2.

This is partial improvement, not a resolution. It also shows why query decomposition should preserve clause-level evidence and explain which subquery contributed each result.


### Five-query approved graded-overlay check

A separate check applied the already human-approved, development-only relevance overlay to the same five calibrated development queries (KDO-RET-002, 014, 020, 024, 044). The calculation uses gain (2^grade - 1), log discount, the explicitly judged pool, and grade 0 for unjudged retrieved records. It does not access holdout data.

| Metric | Baseline TF-IDF | Generic aliases | Relation-aware weighted RRF |
|---|---:|---:|---:|
| Mean judged-pool nDCG@5 | 0.7193 | 0.7774 | **0.7968** |
| KDO-RET-002 | 0.6419 | 0.9324 | **0.9324** |
| KDO-RET-014 | 0.2867 | 0.2867 | **0.3837** |
| KDO-RET-020 | 0.8340 | 0.8340 | 0.8340 |
| KDO-RET-024 | 0.8340 | 0.8340 | 0.8340 |
| KDO-RET-044 | 1.0000 | 1.0000 | 1.0000 |

This confirms a promising improvement on the five calibrated cases, including KDO-RET-014, but that query remains weak: only one of its two gold records is retrieved in the top five. This five-query judged-pool result is a limited diagnostic, not a full-dev graded score or a release gate. It must not be confused with the full 25-query binary nDCG results above.

### Weight sensitivity

A small development-only sweep tested clause weights 0.5, 1.0, 1.5, 2.0, and 3.0. Weight 0.5 reproduced the baseline ranking. Weights 1.5, 2.0, and 3.0 produced identical rankings on these 25 queries; 1.5 is retained as the least aggressive setting in that observed plateau. This is exploratory tuning on development data, not a final parameter selection.

### Engineering boundary

- The module now exposes `decompose_query()` and a separately tested `reciprocal_rank_fusion()` helper.
- Six focused unit tests pass locally.
- The prototype remains offline-only. It is not wired into `KnowledgeBase.search()`, post generation, or publishing.
- Clause extraction is finite and shallow, not full semantic parsing.
- Clause decomposition adds retrieval calls; no hosted latency or cost claim is made.
- Full-dev binary nDCG uses binary expected-gold judgments because complete graded labels are unavailable.
- No holdout queries, production database, production runtime, publishing, or deployment were used.

The candidate is promising enough for another controlled offline evaluation, but **not approved for production or staging runtime integration**.

## Known limitations and next experiment

1. The alias list is deliberately small and static; it cannot infer missing domain concepts reliably.
2. The intent label is shallow lexical classification, not semantic intent understanding.
3. The five-query graded relevance overlay is insufficient for full-dev graded nDCG.
4. Benchmark labels have known calibration limitations.
5. Next, test a constrained compositional concept/phrase model that extracts entities, relations, constraints, and question intent without reading corpus or labels; compare it on the same 25 dev queries and inspect every false-positive rank movement.
6. Keep original-query fallback and query traces mandatory. Do not wire this into `KnowledgeBase.search()` or publishing workflows yet.

## Reproduction

Run the prototype's unit tests with:

```bash
python -m unittest tests.test_experimental_query_understanding
```

The detailed machine-readable run, including all per-query top-five ranked outputs and query traces, is retained as the accompanying experiment artifact.

## Third experiment: bounded, informative-clause retrieval

A follow-up offline run added a deterministic clause gate and query budget. The original query is always retrieved. A clause is eligible only when it has at least three non-stopword terms and at least two terms not present in the other extracted clause(s). At most two eligible clauses are added, selected by a deterministic term-information proxy. This avoids sending weak fragments to retrieval. The candidate uses original-query RRF weight 1.0, clause weight 1.5, RRF k=10, and at most 20 results per subquery.

| Metric | Baseline | Bounded clause candidate |
|---|---:|---:|
| Recall@5 | 0.9400 | **0.9600** |
| Gold hit rate@5 | 0.9600 | **1.0000** |
| MRR | 0.8280 | **0.8560** |
| Binary nDCG@5 | 0.8381 | **0.8623** |
| Hard-negative top-1 rate | 0.2000 | **0.1600** |
| Hard-negative outrank rate | 0.3333 (6/18) | 0.3158 (6/19) |

The bounded candidate reproduces the same top-five rankings as the prior clause-weight-1.5 experiment across all 25 development queries. It uses two retrieval calls for 20 queries and three calls for five queries (2.2 calls/query on average), rather than assuming every query needs every possible clause. KDO-RET-014 still retrieves only one of its two gold records in the top five; its best gold remains at rank 5 and a hard negative remains at rank 2.

### Interpretation and decision

This is a more disciplined retrieval budget, but it is **not yet a runtime recommendation**. The gate is a lexical heuristic; the development set is small; binary judgments do not establish semantic correctness; and no hosted latency or cost has been measured. The unchanged ranking versus the prior weighted-RRF candidate means the gate has demonstrated call selection discipline, not an additional relevance gain. Keep the prototype offline and retain the original-query fallback.

The experiment used the same 25 development queries and 112 knowledge chunks only. No holdout, database, LLM, staging/production runtime, publishing, or deployment was involved.


## Fourth experiment: explicit query-frame extraction (prototype only)

Added `services/experimental_query_frames.py` with a deterministic, explainable frame:
- question intent (causal explanation, procedure, comparison, selection, conditions, etc.);
- original query preserved verbatim;
- token-level concept terms for inspection only;
- explicit operands for the narrow `between X and Y` construction;
- relation markers (contrast, cause/effect, condition, sequence, exclusion, comparison);
- explicit complexity and numeric bounds, plus qualifier spans;
- a negation/exclusion indicator.

Six focused unit tests cover original-query preservation, comparison operands, complexity constraints, exclusion qualifiers, contrast markers, and empty input. Explicit comparison phrases take precedence over generic question openers (for example, “What is the difference between…” is classified as comparison).

**This is not yet an evaluated retrieval strategy.** The extractor is rule-based, intentionally incomplete, and does not infer arbitrary entities or semantic relations. It does not rewrite the original query, expand terms, or access corpus records, gold IDs, labels, databases, or LLMs. No relevance improvement is claimed from this parser. The next evaluation must inspect all 25 development query frames for extraction errors before testing any frame-driven retrieval. Holdout remains untouched, and runtime integration remains prohibited.


### Development-prompt trace audit

A manual trace inspection of all 25 development prompts found two concrete parser defects, both corrected before treating the frame output as stable enough for further research:
1. Questions embedded after context statements (for example, “I need … Which data structure …?”) were missed by an opener-only intent classifier. Intent extraction now detects the interrogative within the full prompt.
2. A comparison operand could absorb a following subordinate condition (for example, “... exactly-once effects when a consumer calls ...”). The operand parser now stops at recognized condition/sequence connectors.

Current rule-based trace counts across the 25 development prompts:
- 6 definition/identification, 11 procedure, 4 causal-explanation, 3 comparison, and 1 selection intent.
- 14 prompts contain at least one recognized relation marker.
- 10 contain a recognized qualifier span.
- 2 explicitly state a supported time-complexity constraint.
- 3 use an explicit “between X and Y” comparison form.

These are parser-coverage counts, not accuracy scores. The finite rules do not capture every implied relation or constraint; for example, a natural-language relationship can exist without a listed connector. The trace audit used development prompts only. No gold labels or record IDs were used to create frames, and no holdout was opened.
