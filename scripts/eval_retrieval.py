#!/usr/bin/env python3
"""Evaluate KDO's current TF-IDF/inverted-index retriever against labeled JSON.

Mirrors KnowledgeBase v3.1 tokenization, IDF, normalized TF-IDF, cosine
scoring, and the runtime content-only document representation. No model
calls are made and evaluation labels are never indexed.
"""
import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path


def record_content(record):
    """Return exactly the text indexed by KnowledgeBase at runtime.

    The production KnowledgeBase tokenizes KnowledgeEntry.content only.
    Structured corpus metadata (title, topics, examples, etc.) is not added
    to the indexed document unless an ingestion caller explicitly flattens
    it into the content field before persistence.
    """
    return record.get("content", "")


STOP_WORDS = {
    "the", "is", "at", "which", "on", "and", "a", "an",
    "in", "to", "for", "of", "with", "by", "from", "as",
    "this", "that", "it", "or", "are", "was", "be", "been",
    "were", "has", "have", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might",
    "can", "shall", "not", "no", "but", "if", "so", "we",
    "you", "he", "she", "they", "them", "their", "our",
    "its", "just", "also", "very", "too", "all", "some",
    "any", "each", "every", "both", "few", "more", "most",
    "other", "such", "only", "own", "same", "than",
    "about", "after", "before", "between", "during",
    "through", "above", "below", "up", "down", "out",
    "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why",
    "how", "what", "who", "whom",
}


def tokenize(text):
    return [token for token in re.findall(r"\w+", text.lower()) if token not in STOP_WORDS]


def build_index(records):
    entries, tokens, postings = {}, {}, defaultdict(set)
    for record in records:
        rid = record.get("record_id") or record.get("id")
        if not rid or rid in entries:
            raise ValueError(f"Missing or duplicate record id: {rid}")
        text = record_content(record) if "record_id" in record else record.get("content", "")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Empty content: {rid}")
        entries[rid] = text
        tokens[rid] = tokenize(text)
        for term in set(tokens[rid]):
            postings[term].add(rid)
    n = len(entries)
    idf = {term: math.log(1.0 + n / (1.0 + len(ids))) for term, ids in postings.items()}
    return {"entries": entries, "tokens": tokens, "postings": postings, "idf": idf, "n": n}


def search(query, index, top_k=5):
    terms = tokenize(query)
    if not terms:
        return []
    postings, idf, n = index["postings"], index["idf"], index["n"]
    candidates = set().union(*(postings.get(term, set()) for term in terms))
    if not candidates:
        return []
    tf = {}
    for term in terms:
        tf[term] = tf.get(term, 0) + 1
    qv = {term: count / len(terms) * idf.get(term, math.log(1 + n / (1 + len(postings.get(term, set()))))) for term, count in tf.items()}
    qnorm = math.sqrt(sum(value * value for value in qv.values()))
    if not qnorm:
        return []
    results = []
    # Sorting IDs before scoring and using an ID tie-break makes evaluation repeatable.
    for rid in sorted(candidates):
        dtf = {}
        tokens = index["tokens"][rid]
        for term in tokens:
            dtf[term] = dtf.get(term, 0) + 1
        dv = {term: count / len(tokens) * idf.get(term, math.log(1 + n / (1 + len(postings.get(term, set()))))) for term, count in dtf.items()}
        dnorm = math.sqrt(sum(value * value for value in dv.values()))
        if not dnorm:
            continue
        score = sum(qv[t] * dv[t] for t in qv if t in dv) / (qnorm * dnorm)
        if score > 0:
            results.append((rid, score))
    return sorted(results, key=lambda item: (-item[1], item[0]))[:top_k]


def evaluate_split(queries, index, top_k=5):
    if not queries:
        return {
            "queries": 0, "recall_at_k": None, "gold_hit_rate_at_k": None,
            "mrr": None, "hard_negative_top1_rate": None,
            "hard_negative_outrank_rate": None, "per_query": [],
        }
    recall, gold_hit, reciprocal, top1_negative, outrank, per_query = [], [], [], [], [], []
    for q in queries:
        ranked = search(q["query"], index, top_k)
        ids = [rid for rid, _ in ranked]
        gold, negatives = set(q.get("expected_record_ids", [])), set(q.get("hard_negative_record_ids", []))
        retrieved_gold = gold.intersection(ids)
        gold_ranks = [ids.index(rid) + 1 for rid in gold if rid in ids]
        negative_ranks = [ids.index(rid) + 1 for rid in negatives if rid in ids]
        best_gold = min(gold_ranks) if gold_ranks else None
        recall.append(len(retrieved_gold) / len(gold) if gold else 0.0)
        gold_hit.append(float(bool(retrieved_gold)))
        reciprocal.append(1 / best_gold if best_gold else 0.0)
        top1_negative.append(float(bool(ids and ids[0] in negatives)))
        if best_gold is not None and negative_ranks:
            outrank.append(float(min(negative_ranks) < best_gold))
        per_query.append({
            "query_id": q.get("query_id"), "query": q["query"],
            "gold_record_ids": sorted(gold), "gold_rank": best_gold,
            "gold_retrieved_count": len(retrieved_gold),
            "gold_count": len(gold),
            "gold_recall_at_k": round(len(retrieved_gold) / len(gold), 4) if gold else 0.0,
            "hard_negative_rank": min(negative_ranks) if negative_ranks else None,
            "top1_is_hard_negative": bool(ids and ids[0] in negatives),
            "ranked_results": [{"record_id": rid, "score": round(score, 6)} for rid, score in ranked],
        })
    return {
        "queries": len(queries),
        "recall_at_k": round(sum(recall) / len(recall), 4),
        "gold_hit_rate_at_k": round(sum(gold_hit) / len(gold_hit), 4),
        "mrr": round(sum(reciprocal) / len(reciprocal), 4),
        "hard_negative_top1_rate": round(sum(top1_negative) / len(top1_negative), 4),
        "hard_negative_outrank_rate": round(sum(outrank) / len(outrank), 4) if outrank else None,
        "hard_negative_outrank_denominator": len(outrank),
        "no_result_queries": sum(not item["ranked_results"] for item in per_query),
        "per_query": per_query,
    }


def evaluate(corpus, evaluation, top_k=5):
    records = [r for r in corpus.get("records", []) if r.get("record_type") == "knowledge_chunk"]
    index = build_index(records)
    available = set(index["entries"])
    missing_gold = sorted({rid for q in evaluation["queries"] for rid in q.get("expected_record_ids", []) if rid not in available})
    missing_negatives = sorted({rid for q in evaluation["queries"] for rid in q.get("hard_negative_record_ids", []) if rid not in available})
    if missing_gold or missing_negatives:
        raise ValueError(f"Corpus coverage incomplete: missing gold={missing_gold}; missing hard negatives={missing_negatives}")
    queries = evaluation["queries"]
    return {
        "retriever": "KDO TF-IDF/inverted-index scoring replica; normalized term frequency and cosine similarity",
        "retriever_version": "knowledge_base.py v3.1 scoring replica; deterministic record_id tie-break",
        "corpus_dataset_id": corpus.get("dataset_id"), "corpus_version": corpus.get("version"),
        "indexed_records": len(records), "evaluation_dataset_id": evaluation.get("dataset_id"),
        "evaluation_version": evaluation.get("version"), "top_k": top_k,
        "coverage": {"gold_ids_missing": missing_gold, "hard_negative_ids_missing": missing_negatives},
        "dev": evaluate_split([q for q in queries if q.get("split") == "dev"], index, top_k),
        "holdout": evaluate_split([q for q in queries if q.get("split") == "holdout"], index, top_k),
        "all": evaluate_split(queries, index, top_k),
        "limitations": [
            "This evaluates the current lexical TF-IDF retriever, not dense-vector embeddings.",
            "Holdout is reported for measurement only and must not be used for tuning.",
            "The benchmark marks its initial judgments as requiring human calibration.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--evaluation", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be >= 1")
    report = evaluate(json.loads(args.corpus.read_text(encoding="utf-8")), json.loads(args.evaluation.read_text(encoding="utf-8")), args.top_k)
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
