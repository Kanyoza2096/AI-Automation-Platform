import unittest

from scripts.experimental_query_understanding import (\n    decompose_query, reciprocal_rank_fusion, understand_query,\n)


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
        self.assertEqual(len(first["normalized_query"].split()),
                         len(set(first["normalized_query"].split())))

    def test_unknown_terms_are_not_invented(self):
        result = understand_query("quasar flibbertigibbet")
        self.assertEqual(result["expansion_terms"], [])
        self.assertEqual(result["normalized_query"], "quasar flibbertigibbet")


if __name__ == "__main__":
    unittest.main()
