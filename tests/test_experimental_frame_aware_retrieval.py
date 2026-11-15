import unittest

from scripts.eval_frame_aware_retrieval import candidate_clauses, evaluate


class FrameAwareRetrievalTests(unittest.TestCase):
    def test_does_not_retrieve_duplicate_of_original_query(self):
        query = "What is a hash table and how does it store keys?"
        self.assertEqual(candidate_clauses(query), [])

    def test_secondary_clause_is_added_only_when_enabled(self):
        query = "What is a hash table and how does it store keys?"
        self.assertEqual(
            candidate_clauses(query, include_secondary=True),
            ["how does it store keys"],
        )

    def test_extracts_explicit_secondary_question(self):
        query = (
            "Why can snapshot isolation allow write skew, "
            "and how do I protect a business invariant?"
        )
        clauses = candidate_clauses(query, include_secondary=True)
        self.assertEqual(clauses, ["how do I protect a business invariant"])

    def test_preserves_relation_decomposition(self):
        query = (
            "Why can evaluation scores improve even though "
            "users correct answers more often?"
        )
        clauses = candidate_clauses(query)
        self.assertEqual(len(clauses), 2)
        self.assertIn("evaluation scores improve", clauses[0])
        self.assertIn("users correct answers more often", clauses[1])

    def test_clause_budget_is_enforced(self):
        query = (
            "Why can evaluation scores improve even though users correct answers more often, "
            "but reviewers still reject the result?"
        )
        clauses = candidate_clauses(query, max_clauses=1)
        self.assertEqual(len(clauses), 1)

    def test_negative_clause_budget_is_rejected(self):
        with self.assertRaises(ValueError):
            candidate_clauses("Why does this happen?", max_clauses=-1)


    def test_evaluator_only_scores_dev_queries(self):
        corpus = {
            "records": [{
                "record_type": "knowledge_chunk",
                "record_id": "gold-record",
                "content": "alpha beta evidence",
            }]
        }
        evaluation = {
            "queries": [
                {
                    "query_id": "DEV-1",
                    "split": "dev",
                    "query": "alpha beta",
                    "expected_record_ids": ["gold-record"],
                    "hard_negative_record_ids": [],
                },
                {
                    "query_id": "SYNTHETIC-HOLDOUT",
                    "split": "holdout",
                    "query": "gamma delta",
                    "expected_record_ids": ["not-indexed-holdout"],
                    "hard_negative_record_ids": [],
                },
            ]
        }
        report = evaluate(corpus, evaluation)
        self.assertEqual(report["scope"]["queries"], 1)
        self.assertFalse(report["scope"]["holdout_evaluated"])

    def test_evaluator_fails_when_dev_gold_is_missing(self):
        corpus = {
            "records": [{
                "record_type": "knowledge_chunk",
                "record_id": "available",
                "content": "alpha beta evidence",
            }]
        }
        evaluation = {
            "queries": [{
                "query_id": "DEV-1",
                "split": "dev",
                "query": "alpha beta",
                "expected_record_ids": ["missing-gold"],
                "hard_negative_record_ids": [],
            }]
        }
        with self.assertRaises(ValueError):
            evaluate(corpus, evaluation)

    def test_evaluator_rejects_empty_dev_split(self):
        corpus = {
            "records": [{
                "record_type": "knowledge_chunk",
                "record_id": "available",
                "content": "alpha beta evidence",
            }]
        }
        evaluation = {"queries": []}
        with self.assertRaises(ValueError):
            evaluate(corpus, evaluation)


if __name__ == "__main__":
    unittest.main()
