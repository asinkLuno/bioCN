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
    This implementation uses dependency parsing (UDEP).
    """

    def __init__(self):
        # Using CLOSE_TOK_POS_NER_SRL_UDEP_SDP_CON_ELECTRA_SMALL_ZH for Universal Dependencies.
        logger.info("Loading HanLP model...")
        import torch
        device = 0 if torch.cuda.is_available() else -1
        if device == 0:
            logger.info("Using GPU for acceleration.")
        else:
            logger.info("Using CPU for inference.")
            
        self.hanlp = hanlp.load(
            hanlp.pretrained.mtl.CLOSE_TOK_POS_NER_SRL_UDEP_SDP_CON_ELECTRA_SMALL_ZH,
            devices=device
        )
        logger.success("HanLP model loaded.")

    def analyze(self, text: str) -> Dict[str, List[Dict[str, str]]]:
        if not text:
            return {}

        sentences = list(split_sentence(text))
        if not sentences:
            return {}

        # Only request necessary tasks to save time
        # Using a reasonable batch_size for small sets of sentences
        docs = self.hanlp(sentences, tasks=['tok/fine', 'dep'], batch_size=32)

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
        # Only request necessary tasks to save time.
        # Larger batch size for large sets of sentences
        docs = self.hanlp(all_sentences, tasks=['tok/fine', 'dep'], batch_size=64)

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

    def _get_phrase(self, tokens: List[str], dep: List, index: int, exclude_indices=None) -> str:
        """
        Reconstruct a phrase for a token by finding its descendants in the dependency tree,
        but exclude adverbial modifiers and other non-core components.
        """
        if exclude_indices is None:
            exclude_indices = set()
        
        # Relations to exclude from the phrase (adverbials, etc.)
        # advmod: adverbial modifier
        # amod: adjectival modifier
        # nmod: noun modifier
        # case: case markers (like '的')
        # punct: punctuation
        exclude_rels = {'advmod', 'amod', 'nmod', 'case', 'mark', 'advcl'}
        
        indices = {index}
        changed = True
        while changed:
            changed = False
            for i, (head, rel) in enumerate(dep):
                base_rel = rel.split(':')[0]
                if head - 1 in indices and i not in indices and i not in exclude_indices:
                    if base_rel not in exclude_rels:
                        indices.add(i)
                        changed = True
        
        sorted_indices = sorted(list(indices))
        # Filter out trailing/leading punctuation
        punctuation = '。，？！；：\'"（）“”‘’'
        while sorted_indices and tokens[sorted_indices[-1]] in punctuation:
            sorted_indices.pop()
        while sorted_indices and tokens[sorted_indices[0]] in punctuation:
            sorted_indices.pop(0)
            
        return "".join([tokens[i] for i in sorted_indices])

    def _extract_svo(
        self, sentences: List[str], docs: Dict
    ) -> Dict[str, List[Dict[str, str]]]:
        """
        Extract SVO (Subject-Verb-Object) structures using Universal Dependencies (DEP).
        Improved accuracy over SRL for grammatical structures.
        """
        sentence_svos = {}

        # HanLP docs can be a list of dicts or a dict of lists
        is_dict_format = isinstance(docs, dict)

        for i, sentence in enumerate(sentences):
            sentence = sentence.strip()
            if not sentence:
                continue

            if is_dict_format:
                tokens = docs.get('tok/fine', docs.get('tok', [[]]))[i]
                dep = docs.get('dep', [[]])[i]
            else:
                doc = docs[i]
                tokens = doc.get('tok/fine', doc.get('tok', []))
                dep = doc.get('dep', [])

            if not tokens or not dep:
                continue

            # 1. Identify all potential predicates and their subjects/objects
            # A predicate is a token that acts as a head for nsubj or obj relations
            predicates = {} # verb_idx -> {'subjects': [], 'objects': []}
            
            # UD relations for subjects and objects
            subject_rels = {'nsubj', 'nsubjpass', 'csubj', 'csubjpass'}
            object_rels = {'obj', 'dobj', 'iobj', 'ccomp', 'xcomp', 'attr'}

            for idx, (head, rel) in enumerate(dep):
                verb_idx = head - 1
                if verb_idx < 0:
                    continue
                
                # Normalize relation (remove language-specific suffixes like :assmod)
                base_rel = rel.split(':')[0]
                
                if base_rel in subject_rels:
                    if verb_idx not in predicates:
                        predicates[verb_idx] = {'subjects': [], 'objects': []}
                    predicates[verb_idx]['subjects'].append(idx)
                elif base_rel in object_rels:
                    if verb_idx not in predicates:
                        predicates[verb_idx] = {'subjects': [], 'objects': []}
                    predicates[verb_idx]['objects'].append(idx)
            
            svo_results = []
            for verb_idx, components in predicates.items():
                # Cross-product of subjects and objects for this predicate
                subjects = components['subjects'] if components['subjects'] else [-1]
                objects = components['objects'] if components['objects'] else [-1]
                
                # For the predicate phrase, exclude descendants that are subjects or objects
                exclude = set(components['subjects']) | set(components['objects'])
                predicate_phrase = self._get_phrase(tokens, dep, verb_idx, exclude_indices=exclude)
                
                if not predicate_phrase:
                    continue

                for s_idx in subjects:
                    for o_idx in objects:
                        # User requested to remove adverbials/modifiers (状语)
                        # We now only use the core token for subject and object
                        subject_phrase = tokens[s_idx] if s_idx != -1 else ""
                        object_phrase = tokens[o_idx] if o_idx != -1 else ""
                        
                        # Only add if we have at least a subject or an object
                        if subject_phrase or object_phrase:
                            svo_results.append({
                                "subject": subject_phrase,
                                "predicate": predicate_phrase,
                                "object": object_phrase
                            })

            if svo_results:
                sentence_svos[sentence] = svo_results
                logger.debug(f"Sentence: {sentence}")
                for result in svo_results:
                    logger.debug(f"  [SVO] S={result['subject']} | V={result['predicate']} | O={result['object']}")

        return sentence_svos
