import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub


class EpubParser:
    """
    A parser for EPUB files to extract and analyze text content with SVO markup.
    """

    def __init__(self, file_path: str, inline_css: bool = True):
        """
        Initializes the parser with the path to the EPUB file.

        Args:
            file_path: The path to the EPUB file.
            inline_css: Whether to use inline CSS styles. Default True for better compatibility.
        """
        self.file_path = file_path
        self.book = epub.read_epub(self.file_path)
        self.inline_css = inline_css

    def get_document_count(self) -> int:
        """
        Returns the number of documents in the EPUB file.
        """
        return len(list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT)))

    def parse_chinese(
        self,
        chinese_analyzer: "ChineseAnalyzer",
        progress: "Progress",
        task: "TaskID",
    ) -> None:
        """
        Extracts and analyzes Chinese text from the EPUB file with SVO markup.
        Marks subjects (blue bold), predicates (underline), objects (green bold).
        Modifies the book object in-place.

        Args:
            chinese_analyzer: The Chinese analyzer for SVO extraction.
            progress: The rich progress bar object.
            task: The rich progress bar task ID.
        """
        # Inject CSS stylesheet first if using external CSS
        if not self.inline_css:
            self._inject_css_stylesheet()

        for item in list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT)):
            soup = BeautifulSoup(item.get_content(), "html.parser")

            paragraphs = soup.find_all("p")
            for p in paragraphs:
                text = p.get_text()
                if not text.strip():
                    continue

                sentence_svos = chinese_analyzer.analyze(text)
                self._mark_svo_in_soup(p, sentence_svos)

            # Update the item content in the book
            item.set_content(str(soup).encode("utf-8"))
            progress.advance(task)

    def save(self, output_path: str) -> None:
        """
        Saves the modified EPUB to the specified path.

        Args:
            output_path: The path where the modified EPUB will be saved.
        """
        epub.write_epub(output_path, self.book, {})

    def _inject_css_stylesheet(self) -> None:
        """
        Injects a CSS stylesheet for SVO highlighting into the EPUB.

        The CSS is added as a new item in the book and linked from all HTML documents.
        """
        css_content = """/* SVO Highlighting Styles */
.svo-subject {
    color: #D95F02;
    font-weight: bold;
}

.svo-predicate {
    color: #1B9E77;
    font-weight: bold;
}

.svo-object {
    color: #7570B3;
    font-weight: bold;
}
"""

        # Create a new CSS item
        css_item = epub.EpubItem(
            uid="svo-styles",
            file_name="style/svo-styles.css",
            media_type="text/css",
            content=css_content,
        )

        # Add the CSS item to the book
        self.book.add_item(css_item)

        # Link the CSS to all HTML documents
        for item in self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            soup = BeautifulSoup(item.get_content(), "html.parser")

            # Check if CSS is already linked
            existing_link = soup.find("link", href="style/svo-styles.css")
            if not existing_link:
                # Find the head element
                head = soup.find("head")
                if head:
                    # Create and append the link element
                    link_tag = soup.new_tag(
                        "link",
                        rel="stylesheet",
                        type="text/css",
                        href="style/svo-styles.css",
                    )
                    head.append(link_tag)

                    # Update the item content
                    item.set_content(str(soup).encode("utf-8"))

    def _mark_svo_in_soup(self, soup: BeautifulSoup, sentence_svos: dict) -> None:
        """
        Marks SVO structures in the BeautifulSoup object using either inline styles or CSS classes.

        Args:
            soup: The BeautifulSoup object to modify.
            sentence_svos: Dictionary mapping sentences to their SVO structures.
        """
        import re

        # Use inline styles or CSS classes based on inline_css flag
        if self.inline_css:
            styles = {
                "subject": 'style="color: #D95F02; font-weight: bold;"',
                "predicate": 'style="color: #1B9E77; font-weight: bold;"',
                "object": 'style="color: #7570B3; font-weight: bold;"',
            }
        else:
            styles = {
                "subject": 'class="svo-subject"',
                "predicate": 'class="svo-predicate"',
                "object": 'class="svo-object"',
            }

        # Collect all text nodes and their parents
        text_nodes = []
        for element in soup.find_all(string=True):
            # Skip script, style tags
            if element.parent.name in ["script", "style"]:
                continue
            text_nodes.append(element)

        # Process each text node
        for element in text_nodes:
            element_text = str(element)
            marked_positions = []  # Track what we've marked to avoid overlaps

            # Collect all SVO components to mark in this text node
            for sentence, svo_list in sentence_svos.items():
                # Only process if this sentence appears in this text node
                if sentence not in element_text:
                    continue

                for svo in svo_list:
                    for component in ["subject", "predicate", "object"]:
                        if not svo[component]:
                            continue

                        text = svo[component]
                        style_attr = styles[component]

                        # Find all occurrences of this text (exact match)
                        for match in re.finditer(re.escape(text), element_text):
                            start, end = match.span()

                            # Check for overlap with already marked positions
                            if not self._has_overlap(start, end, marked_positions):
                                marked_positions.append((start, end, style_attr))

            # Apply markings from back to front to avoid index shifting
            if marked_positions:
                marked_positions.sort(reverse=True)
                new_text = element_text

                for start, end, style_attr in marked_positions:
                    word = new_text[start:end]
                    new_text = (
                        new_text[:start]
                        + f"<span {style_attr}>{word}</span>"
                        + new_text[end:]
                    )

                # Replace the text node with the marked version
                element.replace_with(BeautifulSoup(new_text, "html.parser"))

    def _has_overlap(self, start, end, marked_positions):
        """Check if a position overlaps with any already marked positions."""
        for m_start, m_end, _ in marked_positions:
            if not (end <= m_start or start >= m_end):
                return True
        return False
