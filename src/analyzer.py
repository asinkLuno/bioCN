"""DEP-based SVO annotation of Chinese prose using HanLP (CPU).

Character-level Subject-Verb-Object roles via HanLP's dependency parser
(CTB9 DEP, SD scheme) plus CTB9 POS tagging.

Syntactic relations are mapped straight onto SVO roles, so the labels stay
close to school grammar: ``nsubj``/``top`` -> subject, ``root``/``dep``/
``conj``/``ccomp``/``xcomp``/``rcomp`` (verb POS) -> predicate, ``dobj``/
``range``/``attr`` -> object. Passives surface their true subject through
``nsubjpass`` (SRL never exposed it), and 定语 (``assmod``/``rcmod``/
``det``/``nummod``...) never pollute the core roles, so no DEG/DEC trimming
is needed.

Usage::

    from src.analyzer import ChineseAnalyzer

    analyzer = ChineseAnalyzer()
    segments = analyzer.annotate_batch(["小明吃了一个苹果。"])
    # [{"text": "小明", "role": "subject"}, ...]
"""

from __future__ import annotations

import re

import hanlp
from hanlp.utils.rules import split_sentence
from loguru import logger

_PRIORITY = {"normal": 0, "object": 1, "subject": 2, "predicate": 3}
# CTB9 POS tags that act as real predicates (verbs incl. 形容词谓语句 VA).
_VERB_POS = frozenset({"VV", "VC", "VE", "VA"})
_PRED_RELS = frozenset({"root", "dep", "conj", "ccomp", "xcomp", "rcomp"})
_SUBJ_RELS = frozenset({"nsubj", "top"})
_OBJ_RELS = frozenset({"dobj", "range", "attr"})
_PARA_SPLIT_RE = re.compile(r"(\n\n+)")
_PARA_SEP_RE = re.compile(r"\n\n+")


class ChineseAnalyzer:
    """Annotate Chinese prose with SVO roles using HanLP DEP + POS (CPU)."""

    def __init__(self) -> None:
        logger.info("Loading HanLP TOK+DEP+POS models on CPU...")
        self.tok = hanlp.load(hanlp.pretrained.tok.COARSE_ELECTRA_SMALL_ZH, devices=-1)
        self.dep = hanlp.load(hanlp.pretrained.dep.CTB9_DEP_ELECTRA_SMALL, devices=-1)
        self.pos = hanlp.load(hanlp.pretrained.pos.CTB9_POS_ELECTRA_SMALL, devices=-1)
        logger.success("HanLP TOK+DEP+POS models ready.")

    def annotate_batch(self, proses: list[str]) -> list[list[dict[str, str]]]:
        """Annotate a batch of prose strings efficiently.

        All sentences across all inputs are processed in a single
        inference pass, then results are unflattened per input.
        """
        prose_plans: list[list] = []
        all_sentences: list[str] = []

        for prose in proses:
            if not prose:
                prose_plans.append([])
                continue
            parts = _PARA_SPLIT_RE.split(prose)
            plan: list = []
            for part in parts:
                if _PARA_SEP_RE.fullmatch(part):
                    plan.append(("sep", part))
                    continue
                if not part.strip():
                    plan.append(("empty", part))
                    continue
                sents = [s for s in split_sentence(part.strip()) if s.strip()]
                if not sents:
                    plan.append(("empty", part))
                    continue
                start = len(all_sentences)
                all_sentences.extend(sents)
                plan.append(("para", part, sents, start, start + len(sents)))
            prose_plans.append(plan)

        if all_sentences:
            all_tokens: list[list[str]] = self.tok(all_sentences)
            all_deps: list[list] = self.dep(all_tokens)
            all_pos: list[list[str]] = self.pos(all_tokens)
        else:
            all_tokens, all_deps, all_pos = [], [], []

        results: list[list[dict[str, str]]] = []
        for plan in prose_plans:
            segments: list[dict[str, str]] = []
            for entry in plan:
                kind = entry[0]
                if kind == "sep":
                    segments.append({"text": entry[1], "role": "normal"})
                elif kind == "empty":
                    if entry[1]:
                        segments.append({"text": entry[1], "role": "normal"})
                else:
                    _, part, sents, lo, hi = entry
                    segments.extend(
                        _annotate_paragraph(
                            part,
                            sents,
                            all_tokens[lo:hi],
                            all_deps[lo:hi],
                            all_pos[lo:hi],
                        )
                    )
            results.append(segments)
        return results


def _annotate_paragraph(
    text: str,
    sentences: list[str],
    tok_data: list[list[str]],
    dep_data: list[list],
    pos_data: list[list[str]],
) -> list[dict[str, str]]:
    char_role: list[str] = ["normal"] * len(text)
    search_offset = 0

    for i, sentence in enumerate(sentences):
        pos = text.find(sentence, search_offset)
        if pos == -1:
            continue

        tokens: list[str] = tok_data[i] if i < len(tok_data) else []
        pos_tags: list[str] = pos_data[i] if i < len(pos_data) else []
        deps: list = dep_data[i] if i < len(dep_data) else []
        char_starts = _token_char_starts(sentence, tokens)

        for token_role, c_begin, c_end in _token_spans(
            tokens, deps, pos_tags, char_starts
        ):
            for ci in range(c_begin, min(c_end, len(text))):
                if _PRIORITY[token_role] > _PRIORITY[char_role[ci]]:
                    char_role[ci] = token_role

        search_offset = pos + len(sentence)

    return _to_segments(text, char_role)


def _token_spans(
    tokens: list[str],
    deps: list,
    pos_tags: list[str],
    char_starts: list[int],
) -> list[tuple[str, int, int]]:
    """Yield (role, char_begin, char_end) spans for colored tokens."""
    n = len(tokens)
    if n == 0 or len(deps) < n or len(pos_tags) < n:
        return []
    rels = [d.get("deprel", "") for d in deps]
    heads = [int(d.get("head", 0)) for d in deps]  # 1-based, 0 = root

    # Predicates: verb tokens reachable from the root through PRED_RELS edges
    # only, so dep chains nested inside 定语 (rcmod) never get colored.
    children: list[list[int]] = [[] for _ in range(n + 1)]
    for i, h in enumerate(heads):
        children[h].append(i)
    pred_set: set[int] = set()
    stack = list(children[0])  # token(s) attached to the virtual root
    while stack:
        i = stack.pop()
        if i in pred_set or rels[i] not in _PRED_RELS:
            continue
        pred_set.add(i)
        stack.extend(children[i + 1])
    preds = sorted(i for i in pred_set if pos_tags[i] in _VERB_POS)

    pass_idx = next((i for i in range(n) if rels[i] == "nsubjpass"), -1)
    # 把字句: the model labels both 施事 and the 受事-after-把 as nsubj of the
    # root verb; the ba marker splits them into subject and object.
    ba_idx = next((i for i in range(n) if rels[i] == "ba"), -1)

    def is_pred_head(i: int) -> bool:
        return heads[i] - 1 in pred_set

    subjs: list[int] = []
    objs: list[int] = []
    if pass_idx != -1:
        # 被动句: only the passive subject counts; the 施事 in the 被-phrase
        # (a plain nsubj) is not a syntactic subject.
        subjs = [i for i in range(n) if rels[i] == "nsubjpass"]
    elif ba_idx != -1:
        subjs = [
            i
            for i in range(n)
            if rels[i] in _SUBJ_RELS and i < ba_idx and is_pred_head(i)
        ]
        objs = [
            i
            for i in range(n)
            if rels[i] in _SUBJ_RELS and i > ba_idx and is_pred_head(i)
        ]
    else:
        subjs = [i for i in range(n) if rels[i] in _SUBJ_RELS and is_pred_head(i)]
    objs += [i for i in range(n) if rels[i] in _OBJ_RELS and is_pred_head(i)]

    spans: list[tuple[str, int, int]] = []
    for role, idxs in (("subject", subjs), ("object", objs), ("predicate", preds)):
        for ti in idxs:
            if ti >= len(char_starts):
                continue
            spans.append((role, char_starts[ti], char_starts[ti] + len(tokens[ti])))
    return spans


def _token_char_starts(sentence: str, tokens: list[str]) -> list[int]:
    """Map token indices to char start positions within sentence."""
    starts: list[int] = []
    pos = 0
    for tok in tokens:
        idx = sentence.find(tok, pos)
        if idx == -1:
            starts.append(pos)
        else:
            starts.append(idx)
            pos = idx + len(tok)
    return starts


def _to_segments(text: str, roles: list[str]) -> list[dict[str, str]]:
    if not text:
        return []
    segs: list[dict[str, str]] = []
    start = 0
    cur = roles[0]
    for i in range(1, len(text)):
        if roles[i] != cur:
            segs.append({"text": text[start:i], "role": cur})
            start = i
            cur = roles[i]
    segs.append({"text": text[start:], "role": cur})
    return segs
