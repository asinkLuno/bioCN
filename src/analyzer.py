"""SRL-based SVO annotation of Chinese prose using HanLP (CPU).

Character-level Subject-Verb-Object roles via HanLP's SRL and POS tagging.

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

_ROLE_MAP = {"PRED": "predicate", "ARG0": "subject", "ARG1": "object"}
_PRIORITY = {"normal": 0, "object": 1, "subject": 2, "predicate": 3}
# CTB9 POS tags that count as "real" predicates (verbs only, not adjectives)
_VERB_POS = frozenset({"VV", "VC", "VE"})
_PARA_SPLIT_RE = re.compile(r"(\n\n+)")
_PARA_SEP_RE = re.compile(r"\n\n+")


class ChineseAnalyzer:
    """Annotate Chinese prose with SVO roles using HanLP SRL + POS (CPU)."""

    def __init__(self) -> None:
        logger.info("Loading HanLP TOK+SRL+POS models on CPU...")
        self.tok = hanlp.load(hanlp.pretrained.tok.COARSE_ELECTRA_SMALL_ZH, devices=-1)
        self.srl = hanlp.load("CPB3_SRL_ELECTRA_SMALL", devices=-1)
        self.pos = hanlp.load(hanlp.pretrained.pos.CTB9_POS_ELECTRA_SMALL, devices=-1)
        logger.success("HanLP TOK+SRL+POS models ready.")

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
            all_srl: list[list] = self.srl(all_tokens, tasks="srl")
            all_pos: list[list[str]] = self.pos(all_tokens)
        else:
            all_tokens, all_srl, all_pos = [], [], []

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
                            all_srl[lo:hi],
                            all_pos[lo:hi],
                        )
                    )
            results.append(segments)
        return results


def _annotate_paragraph(
    text: str,
    sentences: list[str],
    tok_data: list[list[str]],
    srl_data: list[list],
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
        char_starts = _token_char_starts(sentence, tokens)
        srl_frames: list = srl_data[i] if i < len(srl_data) else []

        for pas in srl_frames:
            # Each arg: [form, role_label, tok_begin, tok_end] — token indices, end exclusive
            for _form, role_label, tok_begin, tok_end in pas:
                role = _ROLE_MAP.get(role_label)
                if role is None:
                    continue
                if (
                    tok_begin >= len(char_starts)
                    or tok_end > len(tokens)
                    or tok_begin >= tok_end
                ):
                    continue
                c_begin = pos + char_starts[tok_begin]
                c_end = pos + char_starts[tok_end - 1] + len(tokens[tok_end - 1])

                if role == "predicate" and pos_tags:
                    # Only color individual verb tokens (VV/VC/VE) within the span.
                    # Skips leading adverbs (状语, AD) and trailing complements
                    # (补语, DER/LC/direction verbs). Adjective predicates skipped.
                    for ti in range(tok_begin, min(tok_end, len(pos_tags))):
                        if pos_tags[ti] in _VERB_POS and ti < len(char_starts):
                            tc_begin = pos + char_starts[ti]
                            tc_end = tc_begin + len(tokens[ti])
                            for ci in range(tc_begin, min(tc_end, len(text))):
                                if _PRIORITY["predicate"] > _PRIORITY[char_role[ci]]:
                                    char_role[ci] = "predicate"
                    continue

                if role in ("subject", "object") and pos_tags:
                    # Trim leading 定语 by skipping past the last DEG/DEC token.
                    last_de_tok = -1
                    for ti in range(tok_begin, min(tok_end, len(pos_tags))):
                        if pos_tags[ti] in ("DEG", "DEC"):
                            last_de_tok = ti
                    if 0 <= last_de_tok < tok_end - 1:
                        next_tok = last_de_tok + 1
                        if next_tok < len(char_starts):
                            c_begin = pos + char_starts[next_tok]

                for ci in range(c_begin, min(c_end, len(text))):
                    if _PRIORITY[role] > _PRIORITY[char_role[ci]]:
                        char_role[ci] = role

        search_offset = pos + len(sentence)

    return _to_segments(text, char_role)


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
