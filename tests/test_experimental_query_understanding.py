import unittest

from scripts.experimental_query_understanding import (
    decompose_query,
    reciprocal_rank_fusion,
    understand_query,
)


class TestExperimentalQueryUnderstanding(unittest.TestCase):
    def test_preserves_original_and_exposes_trace(self):
        query = "Why does evaluation improve?"
        result = understand_query(query)
        self.assertEqual(result["original_query"], query)
        self.assertEqual(result["intent"], "causal_explanation")
        self.assertIn("better", result["expansion_terms"])
        self.assertTrue(result["normalized_query"].startswith("evaluation improve"))

    def test_expansion_is_deterministic_and_deduplicated(self):
        query = "How can we improve reliability?"
        first = understand_query(query)
        second = understand_query(query)
        self.assertEqual(first, second)
        self.assertEqual(
            len(first["normalized_query"].split()),
            len(set(first["normalized_query"].split())),
        )

    def test_unknown_terms_are_not_invented(self):
        result = understand_query("quasar flibbertigibbet")
        self.assertEqual(result["expansion_terms"], [])
        self.assertEqual(result["normalized_query"], "quasar flibbertigibbet")

    def test_extracts_contrast_clauses_without_losing_original(self):
        query = (
            "Why can evaluation scores improve even though users correct "
            "answers more often?"
        )
        result = decompose_query(query)
        self.assertEqual(result["original_query"], query)
        self.assertEqual(result["intent"], "causal_explanation")
        self.assertEqual(result["relations"], [
            {"connector": "even though", "relation": "contrast"}
        ])
        self.assertEqual(len(result["clauses"]), 2)
        self.assertIn("evaluation scores improve", result["clauses"][0])
        self.assertIn("users correct", result["clauses"][1])

    def test_rank_fusion_respects_explicit_clause_weight(self):
        fused = reciprocal_rank_fusion(
            [["whole-a", "shared"], ["clause-b", "shared"]],
            weights=[1.0, 1.5],
            rrf_k=10,
            top_k=3,
        )
        self.assertEqual(fused[0][0], "shared")
        self.assertGreater(fused[0][1], fused[1][1])

    def test_rank_fusion_rejects_mismatched_weights(self):
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([["a"]], weights=[1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
