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

                if subject and obj:
                    svo_results.append(
                        {"subject": subject, "predicate": verb, "object": obj}
                    )

            if svo_results:
                sentence_svos[sentence] = svo_results

        return sentence_svos
