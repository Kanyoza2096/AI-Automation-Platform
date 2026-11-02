import unittest

from scripts.eval_retrieval import build_index, evaluate, search


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

    def test_metrics_and_splits_are_computed(self):
        report = evaluate(self.corpus, self.evaluation)
        self.assertEqual(report["dev"]["queries"], 1)
        self.assertEqual(report["holdout"]["queries"], 1)
        self.assertEqual(report["all"]["recall_at_5"], 1.0)
        self.assertEqual(report["all"]["mrr"], 1.0)
        self.assertEqual(report["all"]["no_result_queries"], 0)

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
