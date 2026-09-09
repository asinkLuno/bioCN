from __future__ import annotations

from typing import TYPE_CHECKING

import ebooklib
from bs4 import BeautifulSoup, NavigableString
from ebooklib import epub
from loguru import logger

if TYPE_CHECKING:
    from src.analyzer import ChineseAnalyzer

_ROLE_INLINE_STYLE = {
    "subject": "color: #D95F02; font-weight: bold;",
    "predicate": "color: #1B9E77; font-weight: bold;",
    "object": "color: #7570B3; font-weight: bold;",
}


def _collect_paragraphs(documents):
    """Return (docs_data, texts): per-doc non-empty <p> nodes plus their
    flattened text, in document order."""
    docs_data = []
    texts: list[str] = []
    for item in documents:
        soup = BeautifulSoup(item.get_content(), "html.parser")
        valid = []
        for p in soup.find_all("p"):
            text = p.get_text()
            if text.strip():
                valid.append(p)
                texts.append(text)
        docs_data.append((item, soup, valid))
    return docs_data, texts


def _apply_segments(docs_data, segments, progress_callback) -> None:
    """Rewrite each collected <p> with its annotated segments in place."""
    idx = 0
    for item, soup, paragraphs in docs_data:
        logger.info(f"Applying results to document: {item.file_name}")
        for p in paragraphs:
            _rebuild_paragraph(p, segments[idx], soup)
            idx += 1
        item.set_content(str(soup).encode("utf-8"))
        if progress_callback:
            progress_callback()


def _rebuild_paragraph(p, segments: list[dict], soup: BeautifulSoup) -> None:
    """Replace <p> content with segments, wrapping non-normal roles in <span>.
    Inline tags inside <p> are dropped."""
    p.clear()
    for seg in segments:
        role = seg["role"]
        text = seg["text"]
        if not text:
            continue
        if role == "normal":
            p.append(NavigableString(text))
            continue
        span = soup.new_tag("span")
        span["style"] = _ROLE_INLINE_STYLE[role]
        span.string = text
        p.append(span)


class EpubParser:
    """
    A parser for EPUB files to extract and analyze text content with SVO markup.
    """

    def __init__(self, file_path: str):
        """
        Initializes the parser with the path to the EPUB file.

        Args:
            file_path: The path to the EPUB file.
        """
        self.file_path = file_path
        self.book = epub.read_epub(self.file_path)
        self._fix_missing_toc_uids()

    def _fix_missing_toc_uids(self) -> None:
        if not self.book.toc:
            return

        counter = 0

        def fix_items(items):
            nonlocal counter
            for item in items:
                if isinstance(item, (tuple, list)):
                    section, children = item[0], item[1]
                    if hasattr(section, "uid") and section.uid is None:
                        section.uid = f"toc_{counter}"
                        counter += 1
                    fix_items(children)
                elif hasattr(item, "uid") and item.uid is None:
                    item.uid = f"toc_{counter}"
                    counter += 1

        fix_items(self.book.toc)

    def get_document_count(self) -> int:
        """
        Returns the number of documents in the EPUB file.
        """
        return len(list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT)))

    def parse_chinese(
        self,
        chinese_analyzer: "ChineseAnalyzer",
        progress_callback: callable = None,
    ) -> None:
        """
        Extracts and annotates Chinese text from the EPUB file with SVO markup.
        Marks subjects (orange bold), predicates (green bold), objects (purple bold).
        Modifies the book object in-place. Drops inline tags inside <p>.

        Args:
            chinese_analyzer: The Chinese analyzer for SVO annotation.
            progress_callback: Optional callback function called after each document.
        """
        documents = list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
        docs_data, texts = _collect_paragraphs(documents)
        if not texts:
            logger.warning("No Chinese text found in documents.")
            return

        logger.info(f"Total paragraphs to process: {len(texts)}")
        segments = chinese_analyzer.annotate_batch(texts)
        _apply_segments(docs_data, segments, progress_callback)

    def save(self, output_path: str) -> None:
        """
        Saves the modified EPUB to the specified path.

        Args:
            output_path: The path where the modified EPUB will be saved.
        """
        epub.write_epub(output_path, self.book, {})
