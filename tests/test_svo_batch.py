#!/usr/bin/env python3
"""
Test script to verify SVO analyzer batch processing functionality.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer import ChineseAnalyzer


class TestChineseAnalyzerBatch(unittest.TestCase):
    def setUp(self):
        # Mock hanlp.load to avoid loading heavy models during test
        with patch("hanlp.load") as mock_load:
            # Create a mock pipeline that returns a mock document
            mock_pipeline = MagicMock()

            # Setup the mock pipeline behavior
            def side_effect(sentences):
                # sentences is a list of strings
                num_sentences = len(sentences)

                # Mock a simplified doc structure
                # We need 'dep', 'pos/ctb', 'tok/fine'

                # Let's pretend each sentence has 3 words: Subject, Verb, Object
                # e.g. "I love python"

                # dep structure: [(head, rel), ...]
                # 1-based head index. 0 is Root.
                # "I": (2, 'nsubj') -> depends on "love" (idx 2)
                # "love": (0, 'root') -> depends on Root
                # "python": (2, 'dobj') -> depends on "love"

                single_sent_dep = [(2, "nsubj"), (0, "root"), (2, "dobj")]
                single_sent_pos = ["PN", "VV", "NN"]
                single_sent_tok = ["我", "爱", "Python"]

                # If we passed empty list
                if num_sentences == 0:
                    return {"dep": [], "pos/ctb": [], "tok/fine": []}

                return {
                    "dep": [single_sent_dep] * num_sentences,
                    "pos/ctb": [single_sent_pos] * num_sentences,
                    "tok/fine": [single_sent_tok] * num_sentences,
                }

            mock_pipeline.side_effect = side_effect
            mock_load.return_value = mock_pipeline

            self.analyzer = ChineseAnalyzer()
            self.analyzer.hanlp = mock_pipeline

    def test_analyze_single(self):
        """Test the original analyze method."""
        text = "我爱Python。"
        result = self.analyzer.analyze(text)

        # We expect 1 sentence, with SVO: subject=我, predicate=爱, object=Python
        self.assertIn("我爱Python。", result)
        svos = result["我爱Python。"]
        self.assertEqual(len(svos), 1)
        self.assertEqual(svos[0]["subject"], "我")
        self.assertEqual(svos[0]["predicate"], "爱")
        self.assertEqual(svos[0]["object"], "Python")

    def test_analyze_batch(self):
        """Test the new analyze_batch method."""
        texts = [
            "我爱Python。",  # 1 sentence
            "我爱编程。你爱测试。",  # 2 sentences
            "",  # 0 sentences
        ]

        results = self.analyzer.analyze_batch(texts)

        self.assertEqual(len(results), 3)

        # First text
        self.assertEqual(len(results[0]), 1)
        self.assertIn("我爱Python。", results[0])

        # Second text
        # Since our mock returns "我爱Python" structure for everything, keys will be based on input text?
        # Wait, split_sentence("我爱编程。你爱测试。") -> ["我爱编程。", "你爱测试。"]
        # The keys in the result dict are the sentences.
        self.assertEqual(len(results[1]), 2)
        self.assertTrue(any("我爱编程" in k for k in results[1].keys()))

        # Third text
        self.assertEqual(results[2], {})


if __name__ == "__main__":
    unittest.main()
