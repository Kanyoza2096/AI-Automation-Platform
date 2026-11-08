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

    def test_numeric_upper_bound_is_not_an_exclusion(self):
        frame = extract_query_frame("Retry no more than 5 times before failing.")
        self.assertFalse(frame["has_negation_or_exclusion"])
        self.assertIn(
            {"kind": "numeric_bound", "text": "no more than 5"},
            frame["constraints"],
        )

    def test_not_only_idiom_is_not_an_exclusion(self):
        frame = extract_query_frame("Why does caching improve not only speed but also consistency?")
        self.assertFalse(frame["has_negation_or_exclusion"])

    def test_explicit_negation_is_an_exclusion(self):
        frame = extract_query_frame("Why is this operation not idempotent?")
        self.assertTrue(frame["has_negation_or_exclusion"])

    def test_ambiguous_while_is_reported_as_ambiguous(self):
        contrast = extract_query_frame("Why can latency rise while throughput falls?")
        temporal = extract_query_frame("How does the cache refresh while requests arrive?")
        expected = {"connector": "while", "type": "contrast_or_temporal"}
        self.assertIn(expected, contrast["relations"])
        self.assertIn(expected, temporal["relations"])

    def test_when_is_not_forced_into_condition_or_cause(self):
        frame = extract_query_frame("Why can a hash map degrade when many keys collide?")
        self.assertIn(
            {"connector": "when", "type": "temporal_or_condition"},
            frame["relations"],
        )
    def test_rejects_empty_input(self):
        with self.assertRaises(ValueError):
            extract_query_frame("  ")

    def test_detects_question_inside_context_statement(self):
        frame = extract_query_frame(
            "I need range additions and range-sum queries. Which data structure supports lazy updates?"
        )
        self.assertEqual(frame["intent"], "selection")

    def test_comparison_operand_excludes_subordinate_condition(self):
        frame = extract_query_frame(
            "What is the difference between at-least-once delivery and exactly-once effects when a consumer calls an external API?"
        )
        self.assertEqual(frame["comparison_operands"], [
            "at-least-once delivery", "exactly-once effects"
        ])


if __name__ == "__main__":
    unittest.main()
