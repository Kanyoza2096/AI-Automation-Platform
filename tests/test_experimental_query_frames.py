import unittest

from services.experimental_query_frames import extract_query_frame


class QueryFrameTests(unittest.TestCase):
    def test_preserves_original_query_and_identifies_why_intent(self):
        q = "Why can a hash map degrade when many keys collide?"
        frame = extract_query_frame(q)
        self.assertEqual(frame["original_query"], q)
        self.assertEqual(frame["intent"], "causal_explanation")

    def test_extracts_explicit_comparison_operands(self):
        frame = extract_query_frame(
            "What are the trade-offs between B-tree and LSM-tree storage?"
        )
        self.assertEqual(frame["comparison_operands"], [
            "B-tree", "LSM-tree storage"
        ])
        self.assertIn(
            {"connector": "between", "type": "comparison_or_relation"},
            frame["relations"],
        )

    def test_extracts_complexity_constraint(self):
        frame = extract_query_frame(
            "How do I find the next greater element in linear time?"
        )
        self.assertEqual(frame["constraints"], [
            {"kind": "complexity", "text": "linear time"}
        ])

    def test_extracts_qualifier_and_exclusion(self):
        frame = extract_query_frame(
            "How can we compare prompts without executing duplicate payments?"
        )
        self.assertTrue(frame["has_negation_or_exclusion"])
        self.assertTrue(any(x["kind"] == "qualifier"
                            for x in frame["constraints"]))

    def test_extracts_multiple_relation_types(self):
        frame = extract_query_frame(
            "Why can scores improve even though user corrections increase?"
        )
        self.assertEqual(frame["intent"], "causal_explanation")
        self.assertIn(
            {"connector": "even though", "type": "contrast"},
            frame["relations"],
        )

    def test_rejects_empty_input(self):
        with self.assertRaises(ValueError):
            extract_query_frame("  ")


if __ (unittest.main())
