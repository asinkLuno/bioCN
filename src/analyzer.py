"""
Base text analyzer interface and Chinese analyzer implementation.
"""

import logging
from typing import Dict, List

import hanlp
from hanlp.utils.rules import split_sentence

# Set up logging
logging.basicConfig(level=logging.INFO)


class ChineseAnalyzer:
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

            # Find all predicates (not just VV - include VA, VC, VE)
            predicates = self._find_predicates(words, pos_tags, deps)

            for verb_idx in predicates:
                svo = self._extract_svo(verb_idx, deps, words, pos_tags)
                if svo["subject"] or svo["predicate"] or svo["object"]:
                    svo_results.append(svo)

            if svo_results:
                sentence_svos[sentence] = svo_results

        return sentence_svos

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
