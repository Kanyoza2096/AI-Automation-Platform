import unittest

from scripts.eval_retrieval import STOP_WORDS, build_index, evaluate, search, tokenize
from services.knowledge_base import STOP_WORDS as PRODUCTION_STOP_WORDS, tokenize as production_tokenize


class RetrievalEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.corpus = {
            "dataset_id": "test-corpus",
            "version": "1",
            "records": [
                {"record_id": "gold", "record_type": "knowledge_chunk", "title": "B-tree storage", "chapter_title": "Database indexes", "content": "B-tree indexes support ordered lookups and range scans.", "topics": ["database", "storage"], "technologies": ["B-tree"]},
                {"record_id": "negative", "record_type": "knowledge_chunk", "title": "Hash map basics", "chapter_title": "Data structures", "content": "Hash maps use hashing to locate keys and values.", "topics": ["hashing"], "technologies": ["hash map"]},
            ],
        }
        self.evaluation = {
            "dataset_id": "test-eval",
            "version": "1",
            "queries": [
                {"query_id": "q1", "query": "B-tree ordered database range scans", "split": "dev", "expected_record_ids": ["gold"], "hard_negative_record_ids": ["negative"]},
                {"query_id": "q2", "query": "hash maps locate keys with hashing", "split": "holdout", "expected_record_ids": ["negative"], "hard_negative_record_ids": ["gold"]},
            ],
        }

    def test_search_returns_relevant_record_first(self):
        index = build_index([r for r in self.corpus["records"] if r["record_type"] == "knowledge_chunk"])
        self.assertEqual(search("B-tree ordered database range scans", index)[0][0], "gold")

    def test_evaluator_stopwords_match_production_configuration(self):
        self.assertEqual(STOP_WORDS, PRODUCTION_STOP_WORDS)

    def test_evaluator_tokenizer_matches_production_tokenizer(self):
        samples = [
            "The system is designed for reliable event delivery.",
            "Fenwick trees support prefix-sum queries and updates.",
            "Can the index handle range scans between keys?",
        ]
        for sample in samples:
            self.assertEqual(tokenize(sample), production_tokenize(sample))

    def test_stopword_only_query_returns_no_results(self):
        index = build_index(self.corpus["records"])
        self.assertEqual(search("the and is to for", index), [])

    def test_structured_metadata_is_not_indexed_separately(self):
        record = {
            "record_id": "content-only",
            "record_type": "knowledge_chunk",
            "title": "metadata-only-unique-phrase",
            "content": "The stored body describes durable event delivery.",
            "topics": ["metadata-only-unique-topic"],
            "examples": ["metadata-only-unique-example"],
        }
        index = build_index([record])
        self.assertEqual(search("metadata-only-unique-phrase", index), [])
        self.assertEqual(search("metadata-only-unique-topic", index), [])
        self.assertEqual(search("metadata-only-unique-example", index), [])
        self.assertEqual(search("durable event delivery", index)[0][0], "content-only")

    def test_metrics_and_splits_are_computed(self):
        report = evaluate(self.corpus, self.evaluation)
        self.assertEqual(report["dev"]["queries"], 1)
        self.assertEqual(report["holdout"]["queries"], 1)
        self.assertEqual(report["all"]["recall_at_k"], 1.0)
        self.assertEqual(report["all"]["gold_hit_rate_at_k"], 1.0)
        self.assertEqual(report["all"]["mrr"], 1.0)
        self.assertEqual(report["all"]["no_result_queries"], 0)

    def test_recall_at_k_counts_all_relevant_records_not_just_any_hit(self):
        corpus = {
            "dataset_id": "multi-gold-corpus",
            "version": "1",
            "records": [
                {"record_id": "gold-a", "record_type": "knowledge_chunk", "content": "B-tree ordered range scans."},
                {"record_id": "gold-b", "record_type": "knowledge_chunk", "content": "Quantum entanglement and photon polarization."},
            ],
        }
        evaluation = {
            "dataset_id": "multi-gold-eval",
            "version": "1",
            "queries": [{
                "query_id": "multi-gold",
                "query": "B-tree ordered range scans",
                "split": "dev",
                "expected_record_ids": ["gold-a", "gold-b"],
                "hard_negative_record_ids": [],
            }],
        }
        report = evaluate(corpus, evaluation)
        self.assertEqual(report["dev"]["recall_at_k"], 0.5)
        self.assertEqual(report["dev"]["gold_hit_rate_at_k"], 1.0)
        self.assertEqual(report["dev"]["per_query"][0]["gold_retrieved_count"], 1)
        self.assertEqual(report["dev"]["per_query"][0]["gold_count"], 2)

    def test_missing_gold_or_hard_negative_fails_closed(self):
        broken = {
            "dataset_id": "broken-eval",
            "version": "1",
            "queries": [{"query_id": "q", "query": "unknown", "split": "dev", "expected_record_ids": ["absent"], "hard_negative_record_ids": []}],
        }
        with self.assertRaisesRegex(ValueError, "Corpus coverage incomplete"):
            evaluate(self.corpus, broken)

    def test_duplicate_record_ids_are_rejected(self):
        duplicate = [
            {"record_id": "same", "content": "one"},
            {"record_id": "same", "content": "two"},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate record id"):
            build_index(duplicate)


if __name__ == "__main__":
    unittest.main()
