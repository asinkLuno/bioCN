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
    """

    def __init__(self):
        self.hanlp = hanlp.load(
            hanlp.pretrained.mtl.CLOSE_TOK_POS_NER_SRL_DEP_SDP_CON_ELECTRA_SMALL_ZH
        )

    def analyze(self, text: str) -> Dict[str, List[Dict[str, str]]]:
        if not text:
            return {}

        sentences = list(split_sentence(text))
        if not sentences:
            return {}

        # Use SRL (Semantic Role Labeling) for accurate SVO extraction
        docs = self.hanlp(sentences, tasks=["tok", "srl"])

        if "srl" not in docs:
            return {}

        sentence_svos = {}

        for i, predicate_groups in enumerate(docs["srl"]):
            sentence = sentences[i].strip()
            docs["tok/fine"][i]

            svo_results = []

            # Each predicate group contains roles for one predicate
            for roles in predicate_groups:
                # Find the predicate in this group
                predicate = ""
                subject = ""
                obj = ""

                for role_text, role_type, start, end in roles:
                    if role_type == "PRED":
                        predicate = role_text
                    elif role_type == "ARG0":
                        subject = role_text
                    elif role_type == "ARG1":
                        obj = role_text

                if predicate:  # Only add if we found a predicate
                    svo_results.append(
                        {"subject": subject, "predicate": predicate, "object": obj}
                    )

            if svo_results:
                sentence_svos[sentence] = svo_results

        return sentence_svos
