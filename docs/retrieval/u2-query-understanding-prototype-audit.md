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


## Fifth experiment: development-slice intent audit and correction

A second manual inspection reviewed all 25 development prompts (KDO-RET-002 through KDO-RET-050, development split only). It found that the first-question-word classifier was too coarse: several prompts beginning with “What” were actually asking for a decision, a selection, or a set of requirements; some “How” prompts asked for causal explanation rather than a procedure.

The offline parser now uses conservative lexical distinctions:
- “What should …” → decision guidance, unless it asks what a process or object should contain/cover/include/need/require; those are requirements identification.
- “What … method/structure/tool/approach/strategy …” → selection.
- “What … contain/cover/include/need/require …” → requirements identification.
- “How … help/reduce/increase/worsen/protect/enable/allow/cause/lead …” → causal explanation.
- Other “How …” questions remain procedural; “Why …” remains causal.

Regression tests cover these distinctions. This is a correction to the diagnostic intent label only; it does not rewrite queries, change retrieval rankings, or claim semantic understanding. Ambiguous and compound prompts remain a limitation: one primary intent cannot represent every information need in a multi-part question. Relation and qualifier markers are still lexical cues, not resolved semantic roles.

The 25-query inspection also confirmed useful coverage for explicit “between X and Y” operands, linear/logarithmic time constraints, and qualifiers such as “without”, “when”, and “while”. Coverage counts are not accuracy scores. The parser still needs independent human-labelled intent/relation annotations before it can be treated as a validated semantic parser.

The holdout split was not opened. No corpus records, gold IDs, relevance labels, databases, LLMs, staging/production runtime, publishing, or deployment were used. Keep this work offline and unmerged pending test and review evidence.


## Sixth experiment: compound-intent representation and CI correction

The 25-query audit exposed two further gaps.

First, the one-label intent model loses explicit secondary requests in compound questions:
- KDO-RET-028 asks both why snapshot isolation can permit write skew and how to protect a business invariant.
- KDO-RET-042 asks both how RPO and RTO differ and how restore drills should validate backups.

The frame now preserves the primary intent and adds the field secondary_intents for additional explicit question clauses introduced by “and” or a semicolon. This is a shallow lexical extraction, not a complete clause parser. Tests cover both examples.

Second, the first CI run after adding the broader intent tests failed: 37 passed and 13 failed. The failure was traced to incorrectly escaped regular-expression boundaries in _intent(), causing interrogative detection to return information_retrieval. The regex escaping has been corrected. This failure is a real Python test failure, distinct from the unrelated Vercel build-rate-limit status.

The what-should classifier was also refined so questions asking what a process or cursor should contain/cover are labelled requirements_identification, while other “what should” prompts remain decision_guidance. “How should” prompts now distinguish design guidance from other decision guidance.

The focused workflow was updated to run on pushes to this experiment branch as well as pull-request path changes. This makes subsequent verification easier to observe. The newest correction commits still require a fresh successful CI result; do not treat the earlier failed run as resolved until that evidence arrives.

### Updated development-only intent trace

Applying the corrected lexical rules to the 25 development prompts gives this provisional primary-intent distribution:
- 7 causal explanation
- 4 procedure
- 2 decision guidance
- 4 comparison
- 3 selection
- 2 design guidance
- 3 requirements identification

Two prompts (KDO-RET-028 and KDO-RET-042) also contain a separately captured secondary intent. These are heuristic labels and parser coverage counts, not independently measured semantic accuracy. In particular, a single primary intent plus extracted secondary clauses still cannot represent every implicit or nested request.

The audit remains limited to the 25 development queries. The holdout split remains untouched. No corpus records, gold IDs, relevance labels, databases, LLMs, staging/production runtime, publishing, or deployment were used. Keep the prototype offline and unmerged pending fresh CI and further evaluation.


A subsequent CI run then passed 49 tests and failed 2. One failure showed that “How do I protect a business invariant?” was incorrectly classified as causal because the verb “protect” was too broad a causal cue; that cue was removed. The other was an outdated test expectation: “How should tools be designed?” is now correctly asserted as design guidance. A fresh run against the latest commit is still required.


### Fresh CI verification after corrections

The latest head was verified by GitHub Actions:
- Workflow: Knowledge indexing regression tests
- Run: 42501304074
- Job: knowledge-indexing-tests
- Result: success
- Test output: 51 passed in 0.49s

This run includes the query-frame tests, compound secondary-intent cases, and corrected intent rules. The earlier 37/13 and 49/2 failures remain part of the audit trail; they were not hidden or treated as successes. This green run verifies the current tested commit only. It does not validate semantic accuracy or retrieval quality.


## Seventh experiment: frame-aware secondary-intent retrieval ablation

Added an offline runner (scripts/eval_frame_aware_retrieval.py) and focused tests. It compares:
1. Original-query TF-IDF baseline.
2. Relation-aware RRF using unique relation clauses.
3. Frame-aware RRF adding explicit secondary interrogative clauses.

The runner excludes a clause whose normalized tokens are identical to the original query. This corrects an accounting defect in the earlier experiment: when no relation connector existed, the decomposition helper returned the whole query as a “clause”, and the earlier call count counted that duplicate retrieval as an extra call. Rankings were unchanged by removing the duplicate. The relation-aware mean is therefore 1.4 distinct retrieval calls/query, not the previously reported 2.2.

| Metric | Baseline | Relation-aware (deduplicated) | Frame-aware + secondary intents |
|---|---:|---:|---:|
| Recall@5 | 0.9400 | 0.9600 | 0.9600 |
| Gold hit rate@5 | 0.9600 | 1.0000 | 1.0000 |
| MRR | 0.8280 | **0.8560** | 0.8293 |
| Hard-negative top-1 | 0.2000 | 0.1600 | 0.1600 |
| Hard-negative outrank | 0.3333 | 0.3158 | 0.3158 |
| Mean distinct retrieval calls/query | 1.00 | 1.40 | 1.56 |

Four development prompts have explicit secondary interrogative clauses: KDO-RET-028, 042, 044 and 050. Adding those clauses changes the top five for all four, but yields no gold-rank improvement. In KDO-RET-028, the gold record falls from rank 1 to rank 3. The aggregate MRR falls from 0.8560 to 0.8293.

**Decision:** do not automatically retrieve secondary-intent clauses. Preserve them in the query frame as diagnostic information, but let a later, separately validated planning stage decide whether they warrant retrieval. This is an example where richer question interpretation does not automatically improve retrieval.

The reproducible offline runner and focused tests are in the PR. A machine-readable summary is stored at docs/retrieval/calibration/kdo_u2_frame_secondary_intent_ablation_v1.json. All results use 112 knowledge chunks and 25 development queries only. Holdout remains unopened. No hosted latency or cost is claimed.
