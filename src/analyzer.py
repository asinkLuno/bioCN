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
        """Annotate a batch of prose strings in a single inference pass."""
        sentence_sink: list[str] = []
        prose_plans = [_plan_prose(prose, sentence_sink) for prose in proses]

        if sentence_sink:
            all_tokens: list[list[str]] = self.tok(sentence_sink)
            all_deps: list[list] = self.dep(all_tokens)
            all_pos: list[list[str]] = self.pos(all_tokens)
        else:
            all_tokens, all_deps, all_pos = [], [], []

        return [
            _segments_for_plan(plan, sentence_sink, all_tokens, all_deps, all_pos)
            for plan in prose_plans
        ]


def _plan_prose(prose: str, sentence_sink: list[str]) -> list:
    """Split one prose into paragraph entries; sentences go to sink."""
    plan: list = []
    if not prose:
        return plan
    for part in _PARA_SPLIT_RE.split(prose):
        if _PARA_SEP_RE.fullmatch(part):
            plan.append(("sep", part))
        elif not part.strip():
            plan.append(("empty", part))
        else:
            plan.append(_plan_paragraph(part, sentence_sink))
    return plan


def _plan_paragraph(part: str, sentence_sink: list[str]) -> tuple:
    """Split one paragraph into sentences, returning its plan entry.

    Entry: ("para", part, lo, hi) with lo..hi the sentence range in sink.
    """
    sents = [s for s in split_sentence(part.strip()) if s.strip()]
    if not sents:
        return ("empty", part)
    lo = len(sentence_sink)
    sentence_sink.extend(sents)
    return ("para", part, lo, lo + len(sents))


def _segments_for_plan(
    plan, sentences, tok_data, dep_data, pos_data
) -> list[dict[str, str]]:
    """Unflatten per-input results from the shared sentence-level arrays."""
    segments: list[dict[str, str]] = []
    for kind, *rest in plan:
        if kind == "sep":
            segments.append({"text": rest[0], "role": "normal"})
        elif kind == "empty":
            if rest[0]:
                segments.append({"text": rest[0], "role": "normal"})
        else:
            part, lo, hi = rest
            segments.extend(
                _annotate_paragraph(
                    part,
                    sentences[lo:hi],
                    tok_data[lo:hi],
                    dep_data[lo:hi],
                    pos_data[lo:hi],
                )
            )
    return segments


def _annotate_paragraph(
    text, sentences, tok_data, dep_data, pos_data
) -> list[dict[str, str]]:
    """Paint char roles sentence by sentence, then fold into segments."""
    char_role: list[str] = ["normal"] * len(text)
    search_offset = 0

    for i, sentence in enumerate(sentences):
        pos = text.find(sentence, search_offset)
        if pos == -1:
            continue
        tokens = tok_data[i]  # data arrays parallel sentences (batch-built)
        deps = dep_data[i]
        pos_tags = pos_data[i]
        spans = _token_spans(tokens, deps, pos_tags, sentence)
        _fill_roles(char_role, text, pos, spans)
        search_offset = pos + len(sentence)

    return _to_segments(text, char_role)


def _fill_roles(char_role: list[str], text: str, base: int, spans) -> None:
    """Paint character roles within a sentence, honoring priority."""
    for token_role, c_begin, c_end in spans:
        for ci in range(base + c_begin, min(base + c_end, len(text))):
            if _PRIORITY[token_role] > _PRIORITY[char_role[ci]]:
                char_role[ci] = token_role


def _token_spans(tokens, deps, pos_tags, sentence: str) -> list[tuple[str, int, int]]:
    """Yield (role, char_begin, char_end) spans for colored tokens."""
    if not tokens:
        return []
    char_starts = _token_char_starts(sentence, tokens)
    rels = [d["deprel"] for d in deps]
    heads = [int(d["head"]) for d in deps]
    preds = _reachable_pred_idxs(rels, heads, pos_tags)
    subjs, objs = _sv_roles(rels, heads, preds)
    return _span_list(
        (("subject", subjs), ("object", objs), ("predicate", preds)),
        tokens,
        char_starts,
    )


def _children_of(heads: list[int]) -> list[list[int]]:
    """Group token indices by their (1-based) head id."""
    children: list[list[int]] = [[] for _ in range(len(heads) + 1)]
    for i, h in enumerate(heads):
        children[h].append(i)
    return children


def _reachable_pred_idxs(
    rels: list[str], heads: list[int], pos_tags: list[str]
) -> list[int]:
    """Predicate candidates: POS-verbish reachable-from-root tokens.

    Reachability walks PRED_RELS edges only (so dep chains inside 定语
    rcmod stay uncolored) but does not stop at non-verb roots like 被;
    the POS filter is applied last."""
    core = _reachable_core(rels, heads)
    return [i for i in core if pos_tags[i] in _VERB_POS]


def _reachable_core(rels: list[str], heads: list[int]) -> list[int]:
    """Tokens reachable from the root through PRED_RELS edges only."""
    children = _children_of(heads)
    preds: list[int] = []
    stack = list(children[0])
    while stack:
        i = stack.pop()
        if i in preds:
            continue
        if rels[i] not in _PRED_RELS:
            continue
        preds.append(i)
        stack.extend(children[i + 1])
    return preds


def _sv_roles(
    rels: list[str], heads: list[int], preds: list[int]
) -> tuple[list[int], list[int]]:
    """Route to the pattern-specific subject/object splitter."""
    if "nsubjpass" in rels:
        # 被动句: only the passive subject counts; the 施事 in the 被-phrase
        # (a plain nsubj) is not a syntactic subject.
        return _passive_subjs(rels), []
    if "ba" in rels:
        return _ba_roles(rels, heads, preds)
    return _subj_cands(rels, heads, preds), _obj_cands(rels, heads, preds)


def _passive_subjs(rels: list[str]) -> list[int]:
    return [i for i in range(len(rels)) if rels[i] == "nsubjpass"]


def _ba_roles(
    rels: list[str], heads: list[int], preds: list[int]
) -> tuple[list[int], list[int]]:
    """把字句: both 施事 and the 受事-after-把 are nsubj of the root verb;
    the ba marker splits them into subject and object."""
    ba = rels.index("ba")
    subjs: list[int] = []
    objs: list[int] = []
    for i, r in enumerate(rels):
        if r in _SUBJ_RELS:
            if heads[i] - 1 in preds:
                if i < ba:
                    subjs.append(i)
                else:
                    objs.append(i)
    return subjs, objs


def _subj_cands(rels: list[str], heads: list[int], preds: list[int]) -> list[int]:
    """nsubj/top tokens whose head is a predicate."""
    cands: list[int] = []
    for i, r in enumerate(rels):
        if r in _SUBJ_RELS:
            if heads[i] - 1 in preds:
                cands.append(i)
    return cands


def _obj_cands(rels: list[str], heads: list[int], preds: list[int]) -> list[int]:
    """dobj/range/attr tokens whose head is a predicate."""
    cands: list[int] = []
    for i, r in enumerate(rels):
        if r in _OBJ_RELS:
            if heads[i] - 1 in preds:
                cands.append(i)
    return cands


def _span_list(
    groups, tokens: list[str], char_starts: list[int]
) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    for role, idxs in groups:
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
