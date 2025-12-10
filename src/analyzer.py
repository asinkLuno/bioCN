"""
Base text analyzer interface and implementations.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple

import hanlp
from hanlp.utils.rules import split_sentence

# Set up logging
logging.basicConfig(level=logging.INFO)


class BaseAnalyzer(ABC):
    """
    Abstract base class for text analyzers.
    """

    @abstractmethod
    def analyze(self, text: str) -> Dict:
        """
        Analyze text and return extraction results.

        Args:
            text: Input text to analyze

        Returns:
            Dictionary containing analysis results
        """


class ChineseAnalyzer(BaseAnalyzer):
    """
    A Chinese text analyzer using HanLP to extract Subject-Verb-Object structures.
    This implementation uses dependency parsing (UDEP).
    """

    def __init__(self):
        # Using CLOSE_TOK_POS_NER_SRL_UDEP_SDP_CON_ELECTRA_SMALL_ZH for Universal Dependencies.
        self.hanlp = hanlp.load(
            hanlp.pretrained.mtl.CLOSE_TOK_POS_NER_SRL_UDEP_SDP_CON_ELECTRA_SMALL_ZH
        )

    def analyze(self, text: str) -> Dict[str, List[Dict[str, str]]]:
        if not text:
            return {}

        sentences = list(split_sentence(text))
        if not sentences:
            return {}

        # Run all tasks in the pipeline. This is more efficient than looping.
        docs = self.hanlp(sentences)

        sentence_svos = {}

        # The dependency parse tree is in doc['dep']
        # The PoS tags are in doc['pos/ctb']
        for i, sentence in enumerate(sentences):
            sentence = sentence.strip()
            deps = docs["dep"][i]
            pos_tags = docs["pos/ctb"][i]
            words = docs["tok/fine"][i]

            svo_results = []

            # Find verbs and their indices
            verbs = [(j, word) for j, word in enumerate(words) if pos_tags[j] == "VV"]

            for verb_idx, verb in verbs:
                subject = ""
                obj = ""
                # Find subject and object for this verb
                for j, (head, rel) in enumerate(deps):
                    # HanLP dependency heads are 1-based, so adjust verb_idx
                    if head == verb_idx + 1:
                        if rel == "nsubj":
                            subject = words[j]
                        elif rel in ("obj", "dobj"):
                            obj = words[j]

                if subject or verb or obj:
                    svo_results.append(
                        {"subject": subject, "predicate": verb, "object": obj}
                    )

            if svo_results:
                sentence_svos[sentence] = svo_results

        return sentence_svos


class KeywordAnalyzer(BaseAnalyzer):
    """
    A Chinese keyword analyzer using pke_zh library.
    Supports multiple extraction methods: TF-IDF, KeyBERT, TextRank, YAKE.
    """

    def __init__(self, method: str = "tfidf"):
        """
        Initialize the keyword analyzer with specified method.

        Args:
            method: The keyword extraction method ('tfidf', 'keybert', 'textrank', 'yake')
        """
        self.method = method.lower()
        self._initialize_extractor()

    def _initialize_extractor(self):
        """Initialize the appropriate pke_zh extractor based on method."""
        try:
            import pke_zh
        except ImportError:
            raise ImportError(
                "pke_zh is required for keyword extraction. Install with: pip install pke_zh"
            )

        if self.method == "tfidf":
            self.extractor = pke_zh.TfIdf()
        elif self.method == "keybert":
            self.extractor = pke_zh.KeyBert()
        elif self.method == "textrank":
            self.extractor = pke_zh.TextRank()
        elif self.method == "yake":
            self.extractor = pke_zh.Yake()
        else:
            raise ValueError(
                f"Unsupported method: {self.method}. Use 'tfidf', 'keybert', 'textrank', or 'yake'"
            )

    def analyze(self, text: str) -> Dict[str, List[Tuple[str, float, str]]]:
        """
        Analyze text and extract keywords with importance levels.

        Args:
            text: Input text to analyze

        Returns:
            Dictionary mapping text to list of (keyword, score, importance_level) tuples
            importance_level: 'super', 'required', 'important'
        """
        if not text or not text.strip():
            return {"text": []}

        try:
            # Load document into the extractor
            self.extractor.load_document(input=text, language="zh")

            # Select candidates and weight them
            self.extractor.candidate_selection()
            self.extractor.candidate_weighting()

            # Get keywords with scores
            keywords = self.extractor.get_n_best(n=20)

            # Classify keywords by importance level
            classified_keywords = self._classify_importance(keywords)

            return {"text": classified_keywords}

        except Exception as e:
            logging.error(f"Error analyzing text with {self.method}: {e}")
            return {"text": []}

    def _classify_importance(
        self, keywords: List[Tuple[str, float]]
    ) -> List[Tuple[str, float, str]]:
        """
        Classify keywords into importance levels based on their scores.

        Args:
            keywords: List of (keyword, score) tuples

        Returns:
            List of (keyword, score, importance_level) tuples
        """
        if not keywords:
            return []

        # Sort by score (descending)
        sorted_keywords = sorted(keywords, key=lambda x: x[1], reverse=True)

        # Get score range for normalization
        scores = [score for _, score in sorted_keywords]
        if not scores:
            return []

        max_score = max(scores)
        min_score = min(scores)
        score_range = max_score - min_score if max_score != min_score else 1

        # Classify based on normalized score
        classified = []
        for keyword, score in sorted_keywords[:15]:  # Top 15 keywords
            # Normalize score to 0-1 range
            normalized_score = (score - min_score) / score_range

            if normalized_score >= 0.8:
                importance = "super"  # Super important - orange
            elif normalized_score >= 0.5:
                importance = "required"  # Required - purple
            else:
                importance = "important"  # Important - green

            classified.append((keyword, score, importance))

        return classified


class AnalyzerFactory:
    """
    Factory class for creating analyzers based on mode and method.
    """

    @staticmethod
    def create_analyzer(mode: str, method: str = None) -> BaseAnalyzer:
        """
        Create an analyzer instance based on mode and method.

        Args:
            mode: Analysis mode ('svo' or 'keywords')
            method: Extraction method (required for keywords mode)

        Returns:
            Analyzer instance
        """
        mode = mode.lower()

        if mode == "svo":
            return ChineseAnalyzer()
        elif mode == "keywords":
            if not method:
                raise ValueError("Method parameter is required for keywords mode")
            return KeywordAnalyzer(method=method)
        else:
            raise ValueError(f"Unsupported mode: {mode}. Use 'svo' or 'keywords'")
