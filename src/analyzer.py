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

        # Run all tasks in the pipeline.
        docs = self.hanlp(sentences)

        return self._extract_svo(sentences, docs)

    def analyze_batch(self, texts: List[str]) -> List[Dict[str, List[Dict[str, str]]]]:
        """
        Analyze a batch of texts (e.g., paragraphs) for SVO structures.
        Optimized for GPU utilization by processing all sentences in a single batch.
        """
        results = []
        all_sentences = []
        text_sentence_counts = []

        # Pre-process: split all texts into sentences and flatten
        for text in texts:
            if not text:
                text_sentence_counts.append(0)
                continue
            sents = list(split_sentence(text))
            # Remove empty sentences if any
            sents = [s for s in sents if s.strip()]

            if not sents:
                text_sentence_counts.append(0)
                continue

            all_sentences.extend(sents)
            text_sentence_counts.append(len(sents))

        if not all_sentences:
            return [{} for _ in texts]

        # Batch Inference
        # HanLP handles batching internally.
        docs = self.hanlp(all_sentences)

        # Post-process: map back to original texts
        current_idx = 0

        # Check if docs is a dict of lists or list of dicts/docs
        is_dict_format = isinstance(docs, dict)

        for count in text_sentence_counts:
            if count == 0:
                results.append({})
                continue

            batch_sentences = all_sentences[current_idx : current_idx + count]

            # Slice the docs for this batch
            if is_dict_format:
                # Assuming docs is a dictionary where values are lists aligned with input sentences
                batch_docs = {}
                for k, v in docs.items():
                    if isinstance(v, (list, tuple)) and len(v) == len(all_sentences):
                        batch_docs[k] = v[current_idx : current_idx + count]
                    else:
                        # For keys not aligned (e.g. version?), just copy?
                        # Actually _extract_svo only needs 'dep', 'pos/ctb', 'tok/fine'
                        pass
            else:
                # Assume list of docs
                batch_docs = docs[current_idx : current_idx + count]

            svos = self._extract_svo(batch_sentences, batch_docs)
            results.append(svos)

            current_idx += count

        return results

    def _extract_svo(
        self, sentences: List[str], docs: Dict
    ) -> Dict[str, List[Dict[str, str]]]:
        """
        Extract SVO structures from HanLP SRL (Semantic Role Labeling) for a list of sentences.

        SRL output format:
            [[('小明', 'ARG0', 0, 1), ('吃', 'PRED', 1, 2), ('苹果', 'ARG1', 3, 4)], ...]
        """
        sentence_svos = {}

        # SRL is in doc['srl']
        for i, sentence in enumerate(sentences):
            sentence = sentence.strip()
            if not sentence:
                continue

            # Get SRL results for this sentence
            if isinstance(docs, dict):
                srl_results = docs["srl"][i]
            else:
                srl_results = docs[i]["srl"]

            svo_results = []

            # Each element in srl_results is a predicate-argument structure
            for srl_args in srl_results:
                subject = ""
                predicate = ""
                object = ""

                # Extract subject (ARG0), predicate (PRED), and object (ARG1)
                for word, role, start, end in srl_args:
                    if role == "ARG0":
                        subject = word
                    elif role == "PRED":
                        predicate = word
                    elif role == "ARG1":
                        object = word

                # Only add if we have at least a predicate
                if predicate:
                    svo_results.append(
                        {"subject": subject, "predicate": predicate, "object": object}
                    )

            if svo_results:
                sentence_svos[sentence] = svo_results

        return sentence_svos
