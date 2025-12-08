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

        docs = self.hanlp(sentences, tasks="srl")

        # Check if 'srl' key exists
        if "srl" not in docs:
            return {}

        sentence_svos = {}

        # Process each sentence
        for i, srl_for_sentence in enumerate(docs["srl"]):
            sentence = sentences[i].strip()
            svo_results = []

            # srl_for_sentence is a list of predicates found in one sentence
            for roles_for_predicate in srl_for_sentence:
                svo = {"subject": "", "predicate": "", "object": ""}
                # roles_for_predicate is a list of lists/tuples
                for role_info in roles_for_predicate:
                    span, role, _, _ = role_info

                    if role == "PRED":
                        svo["predicate"] = span
                    elif role == "ARG0":
                        svo["subject"] = span
                    elif role == "ARG1":
                        svo["object"] = span

                if svo["predicate"]:
                    svo_results.append(svo)

            if svo_results:
                sentence_svos[sentence] = svo_results

        return sentence_svos
