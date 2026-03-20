"""
Base text analyzer interface and Chinese analyzer implementation.
"""

from typing import Dict, List

import hanlp
from hanlp.utils.rules import split_sentence
from loguru import logger


class ChineseAnalyzer:
    """
    A Chinese text analyzer using HanLP to extract Subject-Verb-Object structures.
    This implementation uses Semantic Role Labeling (SRL).
    """

    def __init__(self):
        logger.info("Loading HanLP model...")
        import torch

        device = 0 if torch.cuda.is_available() else -1
        if device == 0:
            logger.info("Using GPU for acceleration.")
        else:
            logger.info("Using CPU for inference.")

        self.hanlp = hanlp.load(
            hanlp.pretrained.mtl.CLOSE_TOK_POS_NER_SRL_UDEP_SDP_CON_ELECTRA_SMALL_ZH,
            devices=device,
        )
        logger.success("HanLP model loaded.")

    def analyze(self, text: str) -> Dict[str, List[Dict[str, str]]]:
        if not text:
            return {}

        sentences = list(split_sentence(text))
        if not sentences:
            return {}

        docs = self.hanlp(sentences, tasks=["srl"], batch_size=32)
        return self._extract_svo(sentences, docs)

    def analyze_batch(self, texts: List[str]) -> List[Dict[str, List[Dict[str, str]]]]:
        """
        Analyze a batch of texts (e.g., paragraphs) for SVO structures.
        Optimized for GPU utilization by processing all sentences in a single batch.
        """
        all_sentences = []
        text_sentence_counts = []

        for text in texts:
            if not text:
                text_sentence_counts.append(0)
                continue
            sents = [s for s in split_sentence(text) if s.strip()]
            if not sents:
                text_sentence_counts.append(0)
                continue
            all_sentences.extend(sents)
            text_sentence_counts.append(len(sents))

        if not all_sentences:
            return [{} for _ in texts]

        docs = self.hanlp(all_sentences, tasks=["srl"], batch_size=64)

        results = []
        current_idx = 0
        for count in text_sentence_counts:
            if count == 0:
                results.append({})
                continue
            batch_sentences = all_sentences[current_idx : current_idx + count]
            batch_srl = docs["srl"][current_idx : current_idx + count]
            results.append(self._extract_svo(batch_sentences, {"srl": batch_srl}))
            current_idx += count

        return results

    def _extract_svo(
        self, sentences: List[str], docs: Dict
    ) -> Dict[str, List[Dict[str, str]]]:
        sentence_svos = {}
        srl_data = docs.get("srl", [])

        for i, sentence in enumerate(sentences):
            sentence = sentence.strip()
            if not sentence or i >= len(srl_data):
                continue

            svo_results = []
            for pas in srl_data[i]:
                predicate, subject, obj = "", "", ""
                for form, role, _begin, _end in pas:
                    if role == "PRED":
                        predicate = form
                    elif role == "ARG0":
                        subject = form
                    elif role == "ARG1":
                        obj = form

                if predicate and (subject or obj):
                    svo_results.append(
                        {"subject": subject, "predicate": predicate, "object": obj}
                    )

            if svo_results:
                sentence_svos[sentence] = svo_results
                logger.debug(f"Sentence: {sentence}")
                for result in svo_results:
                    logger.debug(
                        f"  [SVO] S={result['subject']} | V={result['predicate']} | O={result['object']}"
                    )

        return sentence_svos
