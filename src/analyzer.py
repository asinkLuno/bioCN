"""
Base text analyzer interface and Chinese analyzer implementation.
"""

import logging
from typing import Dict, List

import hanlp
from hanlp.utils.rules import split_sentence

try:
    import torch
except ImportError:
    torch = None

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ChineseAnalyzer:
    """
    A Chinese text analyzer using HanLP to extract Subject-Verb-Object structures.
    This implementation uses dependency parsing (UDEP).
    """

    def __init__(self):
        # Explicitly manage GPU/CPU device selection
        if torch is not None and torch.cuda.is_available():
            device = 0
            logger.info("✓ Using GPU for acceleration")
        else:
            device = None
            if torch is None:
                logger.warning(
                    "⚠ PyTorch not found - using CPU (will be significantly slower)"
                )
            else:
                logger.warning("⚠ GPU not available - using CPU (will be slower)")

        # Using CLOSE_TOK_POS_NER_SRL_UDEP_SDP_CON_ELECTRA_SMALL_ZH for Universal Dependencies.
        self.hanlp = hanlp.load(
            hanlp.pretrained.mtl.CLOSE_TOK_POS_NER_SRL_UDEP_SDP_CON_ELECTRA_SMALL_ZH,
            devices=device,
        )

    def analyze(self, text: str) -> Dict[str, List[Dict[str, str]]]:
        """Analyze a single text. For multiple texts, use batch_analyze()."""
        results = self.batch_analyze([text])
        return results[0] if results else {}

    def batch_analyze(self, texts: List[str]) -> List[Dict[str, List[Dict[str, str]]]]:
        """
        Batch analyze multiple texts - significantly faster on GPU than calling analyze() in a loop.

        Args:
            texts: List of text strings to analyze.

        Returns:
            List of dictionaries, each mapping sentences to their SVO structures.
        """
        if not texts:
            return []

        # Collect all sentences from all texts, tracking boundaries
        all_sentences = []
        text_boundaries = [0]

        for text in texts:
            if not text:
                text_boundaries.append(text_boundaries[-1])
                continue

            sentences = list(split_sentence(text))
            all_sentences.extend(sentences)
            text_boundaries.append(len(all_sentences))

        # Single GPU call for all sentences - this is the key optimization
        if not all_sentences:
            return [{}] * len(texts)

        docs = self.hanlp(all_sentences)

        # Split results back by text boundaries
        results = []
        for i in range(len(texts)):
            start, end = text_boundaries[i], text_boundaries[i + 1]

            if start == end:  # Empty text
                results.append({})
                continue

            # Extract SVO for this text's sentences
            sentence_svos = {}
            text_sentences = all_sentences[start:end]

            for j, sentence in enumerate(text_sentences):
                sentence = sentence.strip()
                doc_idx = start + j
                deps = docs["dep"][doc_idx]
                pos_tags = docs["pos/ctb"][doc_idx]
                words = docs["tok/fine"][doc_idx]

                svo_results = []
                predicates = self._find_predicates(words, pos_tags, deps)

                for verb_idx in predicates:
                    svo = self._extract_svo(verb_idx, deps, words, pos_tags)
                    if svo["subject"] or svo["predicate"] or svo["object"]:
                        svo_results.append(svo)

                if svo_results:
                    sentence_svos[sentence] = svo_results

            results.append(sentence_svos)

        return results

    def _find_predicates(self, words, pos_tags, deps):
        """Find all predicate indices. Support VV, VA, VC, VE."""
        VERB_TAGS = {"VV", "VA", "VC", "VE"}
        return [i for i, tag in enumerate(pos_tags) if tag in VERB_TAGS]

    def _extract_svo(self, verb_idx, deps, words, pos_tags):
        """Extract subject, predicate, object for a given verb."""
        subject = self._find_argument(
            verb_idx, deps, words, pos_tags, {"nsubj", "nsubj:pass", "csubj"}
        )
        obj = self._find_argument(
            verb_idx, deps, words, pos_tags, {"obj", "dobj", "iobj", "ccomp"}
        )
        predicate = self._extract_phrase(verb_idx, deps, words, pos_tags)

        return {"subject": subject, "predicate": predicate, "object": obj}

    def _find_argument(self, verb_idx, deps, words, pos_tags, rel_types):
        """Find subject or object for a verb based on dependency relations."""
        for j, (head, rel) in enumerate(deps):
            # HanLP dependency heads are 1-based
            if head == verb_idx + 1 and rel in rel_types:
                return self._extract_phrase(j, deps, words, pos_tags)
        return ""

    def _extract_phrase(self, head_idx, deps, words, pos_tags):
        """
        Recursively extract a complete phrase from the dependency tree.
        Includes modifiers like adjectives, determiners, numerals.
        """
        phrase_parts = [(head_idx, words[head_idx])]

        # Find all dependents of this head
        MODIFIER_RELS = {
            "amod",  # adjectival modifier
            "det",  # determiner
            "nummod",  # numeric modifier
            "compound",  # compound
            "nmod",  # nominal modifier
            "case",  # case marking
            "advmod",  # adverbial modifier
            "aux",  # auxiliary
        }

        for j, (head, rel) in enumerate(deps):
            # HanLP dependency heads are 1-based
            if head == head_idx + 1 and rel in MODIFIER_RELS:
                phrase_parts.append((j, words[j]))

        # Sort by index to maintain word order
        phrase_parts.sort(key=lambda x: x[0])

        return "".join([word for _, word in phrase_parts])
