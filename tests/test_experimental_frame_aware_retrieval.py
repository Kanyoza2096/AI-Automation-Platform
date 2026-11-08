import unittest

from scripts.eval_frame_aware_retrieval import candidate_clauses


class FrameAwareRetrievalTests(unittest.TestCase):
    def test_does_not_retrieve_duplicate_of_original_query(self):
        query = "What is a hash table and how does it store keys?"
        clauses = candidate_clauses(query)
        self.assertNotIn(" ".join(clauses[0].lower().split()) if clauses else "", {
            " ".join(query.lower().split())
        })

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


if __name__ == "__main__":
    unittest.main()
