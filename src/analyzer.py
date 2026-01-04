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

        sentence_svos = {}

        # The dependency parse tree is in doc['dep']
        # The PoS tags are in doc['pos/ctb']
        for i, sentence in enumerate(sentences):
            sentence = sentence.strip()
            deps = docs["dep"][i]
            pos_tags = docs["pos/ctb"][i]
            words = docs["tok/fine"][i]

            # Helper to find children of a node with specific relations
            def get_children(head_idx: int, rels: List[str]) -> List[int]:
                # head_idx is 0-based
                children = []
                for j, (h, r) in enumerate(deps):
                    if h == head_idx + 1 and r in rels:
                        children.append(j)
                return children

            # Helper to expand coordinates (conj)
            def expand_conj(idx: int) -> List[int]:
                indices = [idx]
                # Find children with 'conj' relation
                # BFS or recursive
                queue = [idx]
                visited = {idx}
                while queue:
                    curr = queue.pop(0)
                    conjs = get_children(curr, ["conj"])
                    for c in conjs:
                        if c not in visited:
                            visited.add(c)
                            indices.append(c)
                            queue.append(c)
                # Also check if this node is a 'conj' of another node?
                # Usually we just look down the tree for 'conj' children in UD.
                return sorted(indices)

            svo_results = []

            # Find verbs (VV)
            verbs = [(j, word) for j, word in enumerate(words) if pos_tags[j] == "VV"]

            for verb_idx, verb in verbs:
                subjects = []
                objects = []

                # 1. Direct Subject (nsubj)
                subj_indices = get_children(verb_idx, ["nsubj"])
                if not subj_indices:
                    # Try shared subject if this verb is a conjunct of another verb
                    # Check if this verb has a 'conj' head
                    head_idx = deps[verb_idx][0] - 1
                    head_rel = deps[verb_idx][1]
                    if head_idx >= 0 and head_rel == "conj":
                        # The head verb might have a subject
                        # We recursively look up the chain, but for simplicity, just check the immediate head
                        subj_indices = get_children(head_idx, ["nsubj"])

                for idx in subj_indices:
                    subjects.extend(expand_conj(idx))

                # 2. Direct Object (dobj, obj)
                obj_indices = get_children(verb_idx, ["dobj", "obj"])
                for idx in obj_indices:
                    objects.extend(expand_conj(idx))

                # 3. Special Case: Passive (Bei)
                # Check if verb depends on LB/SB/Bei
                # The verb is usually a dependent of '被' in some parses, or '被' is aux
                # In the tested model: Verb --[dep]--> Bei(Root)
                head_idx = deps[verb_idx][0] - 1
                if head_idx >= 0:
                    head_pos = pos_tags[head_idx]
                    if head_pos in ("LB", "SB") or words[head_idx] == "被":
                        # Found passive marker as head.
                        # The 'nsubjpass' of the marker is the Patient (Logical Object)
                        pass_subjs = get_children(head_idx, ["nsubjpass", "nsubj:pass"])
                        for idx in pass_subjs:
                            objects.extend(expand_conj(idx))

                        # Note: In this structure, the agent is often the 'nsubj' of the verb itself.
                        # We already collected 'nsubj' above into 'subjects'.
                        # e.g., "苹果被我吃了" -> 我(nsubj of 吃), 苹果(nsubjpass of 被)

                # 4. Special Case: Ba-construction
                # Check for aux:ba
                ba_indices = get_children(verb_idx, ["aux:ba"])
                if ba_indices:
                    # '把' is present.
                    # If we didn't find a direct object, look for 'dep' dependencies that are nouns
                    if not objects:
                        # Look for 'dep' or 'obl' or 'compound:dir' that are Nouns (NN, NR)
                        # excluding the subject we already found
                        candidates = get_children(verb_idx, ["dep", "obl", "dobj"])
                        # Filter for nouns and not in subjects
                        for idx in candidates:
                            if idx not in subjects and pos_tags[idx] in (
                                "NN",
                                "NR",
                                "PN",
                            ):
                                objects.extend(expand_conj(idx))

                # Format results
                subj_str = "、".join([words[i] for i in sorted(list(set(subjects)))])
                obj_str = "、".join([words[i] for i in sorted(list(set(objects)))])

                if subj_str or verb or obj_str:
                    svo_results.append(
                        {"subject": subj_str, "predicate": verb, "object": obj_str}
                    )

            if svo_results:
                sentence_svos[sentence] = svo_results

        return sentence_svos
