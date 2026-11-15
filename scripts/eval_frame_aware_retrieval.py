#!/usr/bin/env python3
"""Offline ablation of query-frame secondary intents for retrieval.

Evaluates the 25 development queries only. The holdout split is never passed
to a retrieval strategy. No database, LLM, network, or runtime calls occur.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from scripts.eval_retrieval import build_index, search, tokenize
from scripts.experimental_query_understanding import (
    decompose_query,
    reciprocal_rank_fusion,
)
from services.experimental_query_frames import extract_query_frame


def candidate_clauses(query: str, include_secondary: bool = False,
                      max_clauses: int = 2) -> list[str]:
    """Return unique informative subqueries, excluding a duplicate full query."""
    if max_clauses < 0:
        raise ValueError("max_clauses must be non-negative")

    original_key = " ".join(tokenize(query))
    seen = {original_key}
    clauses: list[str] = []

    for clause in decompose_query(query).get("clauses", []):
        key = " ".join(tokenize(clause))
        if key and key not in seen:
            seen.add(key)
            clauses.append(clause)

    if include_secondary:
        frame = extract_query_frame(query)
        for item in frame.get("secondary_intents", []):
            clause = re.sub(r"^(?:and|;)\s*", "", item.get("clause", ""),
                            flags=re.IGNORECASE).strip(" ,")
            key = " ".join(tokenize(clause))
            if key and key not in seen:
                seen.add(key)
                clauses.append(clause)

    return clauses[:max_clauses]


def rank_query(query: str, index: dict, include_secondary: bool,
               top_k: int = 5, depth: int = 20) -> dict:
    clauses = candidate_clauses(query, include_secondary=include_secondary)
    ranked_lists = [
        [record_id for record_id, _ in search(query, index, depth)]
    ]
    for clause in clauses:
        ranked_lists.append([
            record_id for record_id, _ in search(clause, index, depth)
        ])

    fused = reciprocal_rank_fusion(
        ranked_lists,
        weights=[1.0] + [1.5] * len(clauses),
        rrf_k=10,
        top_k=top_k,
    )
    return {
        "ranked_results": [
            {"record_id": record_id, "score": round(score, 8)}
            for record_id, score in fused
        ],
        "retrieval_calls": len(ranked_lists),
        "subqueries": clauses,
    }


def evaluate_strategy(queries: list[dict], index: dict,
                      strategy: str, top_k: int = 5,
                      depth: int = 20) -> dict:
    recalls, hits, reciprocal, negative_top1, outrank = [], [], [], [], []
    per_query = []
    total_calls = 0

    for query in queries:
        if strategy == "baseline":
            ranked = search(query["query"], index, top_k)
            result = {
                "ranked_results": [
                    {"record_id": rid, "score": round(score, 8)}
                    for rid, score in ranked
                ],
                "retrieval_calls": 1,
                "subqueries": [],
            }
        else:
            result = rank_query(
                query["query"], index,
                include_secondary=(strategy == "frame_aware"),
                top_k=top_k, depth=depth,
            )

        total_calls += result["retrieval_calls"]
        ids = [item["record_id"] for item in result["ranked_results"]]
        gold = set(query.get("expected_record_ids", []))
        negatives = set(query.get("hard_negative_record_ids", []))
        retrieved_gold = gold.intersection(ids)
        gold_ranks = [ids.index(rid) + 1 for rid in gold if rid in ids]
        negative_ranks = [
            ids.index(rid) + 1 for rid in negatives if rid in ids
        ]
        best_gold = min(gold_ranks) if gold_ranks else None
        best_negative = min(negative_ranks) if negative_ranks else None

        recalls.append(len(retrieved_gold) / len(gold) if gold else 0.0)
        hits.append(float(bool(retrieved_gold)))
        reciprocal.append(1 / best_gold if best_gold else 0.0)
        negative_top1.append(float(bool(ids and ids[0] in negatives)))
        if best_gold is not None and best_negative is not None:
            outrank.append(float(best_negative < best_gold))

        per_query.append({
            "query_id": query.get("query_id"),
            "gold_rank": best_gold,
            "hard_negative_rank": best_negative,
            "gold_retrieved_count": len(retrieved_gold),
            "gold_count": len(gold),
            "retrieval_calls": result["retrieval_calls"],
            "subqueries": result["subqueries"],
            "ranked_results": result["ranked_results"],
        })

    count = len(queries)
    return {
        "queries": count,
        "recall_at_5": round(sum(recalls) / count, 4) if count else None,
        "gold_hit_rate_at_5": round(sum(hits) / count, 4) if count else None,
        "mrr": round(sum(reciprocal) / count, 4) if count else None,
        "hard_negative_top1_rate": (
            round(sum(negative_top1) / count, 4) if count else None
        ),
        "hard_negative_outrank_rate": (
            round(sum(outrank) / len(outrank), 4) if outrank else None
        ),
        "hard_negative_outrank_denominator": len(outrank),
        "mean_retrieval_calls": round(total_calls / count, 4) if count else None,
        "per_query": per_query,
    }


def evaluate(corpus: dict, evaluation: dict) -> dict:
    records = [
        record for record in corpus.get("records", [])
        if record.get("record_type") == "knowledge_chunk"
    ]
    index = build_index(records)
    # Intentionally pass only dev queries to any evaluation function.
    dev_queries = [
        query for query in evaluation.get("queries", [])
        if query.get("split") == "dev"
    ]
    if not dev_queries:
        raise ValueError("evaluation contains no development queries")

    available = set(index["entries"])
    missing_gold = sorted({
        record_id
        for query in dev_queries
        for record_id in query.get("expected_record_ids", [])
        if record_id not in available
    })
    missing_negatives = sorted({
        record_id
        for query in dev_queries
        for record_id in query.get("hard_negative_record_ids", [])
        if record_id not in available
    })
    if missing_gold or missing_negatives:
        raise ValueError(
            f"Corpus coverage incomplete: missing gold={missing_gold}; "
            f"missing hard negatives={missing_negatives}"
        )

    return {
        "experiment": "U2 frame-aware secondary-intent retrieval ablation",
        "scope": {
            "split": "dev",
            "queries": len(dev_queries),
            "holdout_evaluated": False,
            "indexed_knowledge_chunks": len(records),
            "top_k": 5,
            "subquery_depth": 20,
        },
        "strategies": {
            "baseline": evaluate_strategy(
                dev_queries, index, "baseline"
            ),
            "relation_aware": evaluate_strategy(
                dev_queries, index, "relation_aware"
            ),
            "frame_aware": evaluate_strategy(
                dev_queries, index, "frame_aware"
            ),
        },
        "interpretation": [
            "Secondary intents are diagnostic features, not automatic retrieval instructions.",
            "Compare MRR and hard-negative movement, not recall alone.",
            "This lexical replica does not measure dense retrieval, hosted latency, or service cost.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--evaluation", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(
        json.loads(args.corpus.read_text(encoding="utf-8")),
        json.loads(args.evaluation.read_text(encoding="utf-8")),
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
