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
        all_docs_data = []
        all_flattened_texts: list[str] = []

        logger.info("Gathering all paragraphs for batch processing...")
        for item in documents:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            paragraphs = soup.find_all("p")
            valid_paragraphs = []
            paragraph_texts = []

            for p in paragraphs:
                text = p.get_text()
                if text.strip():
                    valid_paragraphs.append(p)
                    paragraph_texts.append(text)

            all_docs_data.append((item, soup, valid_paragraphs))
            all_flattened_texts.extend(paragraph_texts)

        if not all_flattened_texts:
            logger.warning("No Chinese text found in documents.")
            return

        logger.info(f"Total paragraphs to process: {len(all_flattened_texts)}")
        all_segments = chinese_analyzer.annotate_batch(all_flattened_texts)

        result_idx = 0
        for item, soup, valid_paragraphs in all_docs_data:
            logger.info(f"Applying results to document: {item.file_name}")
            for p in valid_paragraphs:
                self._rebuild_paragraph(p, all_segments[result_idx], soup)
                result_idx += 1
            item.set_content(str(soup).encode("utf-8"))
            if progress_callback:
                progress_callback()

    def save(self, output_path: str) -> None:
        """
        Saves the modified EPUB to the specified path.

        Args:
            output_path: The path where the modified EPUB will be saved.
        """
        epub.write_epub(output_path, self.book, {})

    def _rebuild_paragraph(self, p, segments: list[dict], soup: BeautifulSoup) -> None:
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
