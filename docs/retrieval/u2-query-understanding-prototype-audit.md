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

The local Python evaluation measured approximately 4.43 ms/query for baseline and 4.34 ms/query for candidate. This is a small local wall-clock measurement including evaluation work, not hosted request latency or an isolated production query-planning benchmark.

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