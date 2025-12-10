import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub


class EpubParser:
    """
    A parser for EPUB files to extract and analyze text content with markup.
    """

    def __init__(self, file_path: str, mode: str = "svo"):
        """
        Initializes the parser with the path to the EPUB file.

        Args:
            file_path: The path to the EPUB file.
            mode: Analysis mode ('svo' or 'keywords')
        """
        self.file_path = file_path
        self.mode = mode.lower()
        self.book = epub.read_epub(self.file_path)

    def get_document_count(self) -> int:
        """
        Returns the number of documents in the EPUB file.
        """
        return len(list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT)))

    def parse_text(
        self,
        analyzer: "BaseAnalyzer",
        progress: "Progress",
        task: "TaskID",
    ) -> None:
        """
        Extracts and analyzes text from the EPUB file with markup.
        Modifies the book object in-place.

        Args:
            analyzer: The text analyzer for extraction.
            progress: The rich progress bar object.
            task: The rich progress bar task ID.
        """
        # Inject CSS stylesheet first
        self._inject_css_stylesheet()

        for item in list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT)):
            soup = BeautifulSoup(item.get_content(), "html.parser")

            paragraphs = soup.find_all("p")
            for p in paragraphs:
                text = p.get_text()
                if not text.strip():
                    continue

                analysis_results = analyzer.analyze(text)

                if self.mode == "svo":
                    self._mark_svo_in_soup(p, analysis_results)
                elif self.mode == "keywords":
                    self._mark_keywords_in_soup(p, analysis_results)

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
        Injects a CSS stylesheet for highlighting into the EPUB.

        The CSS is added as a new item in the book and linked from all HTML documents.
        """
        if self.mode == "svo":
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
            css_uid = "svo-styles"
            css_filename = "style/svo-styles.css"
        elif self.mode == "keywords":
            css_content = """/* Keyword Highlighting Styles */
.keyword-super {
    color: #FF6B35;
    font-weight: bold;
}

.keyword-required {
    color: #7570B3;
    font-weight: bold;
}

.keyword-important {
    color: #1B9E77;
    font-weight: bold;
}
"""
            css_uid = "keyword-styles"
            css_filename = "style/keyword-styles.css"

        # Create a new CSS item
        css_item = epub.EpubItem(
            uid=css_uid,
            file_name=css_filename,
            media_type="text/css",
            content=css_content,
        )

        # Add the CSS item to the book
        self.book.add_item(css_item)

        # Link the CSS to all HTML documents
        for item in self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            soup = BeautifulSoup(item.get_content(), "html.parser")

            # Check if CSS is already linked
            existing_link = soup.find("link", href=css_filename)
            if not existing_link:
                # Find the head element
                head = soup.find("head")
                if head:
                    # Create and append the link element
                    link_tag = soup.new_tag(
                        "link",
                        rel="stylesheet",
                        type="text/css",
                        href=css_filename,
                    )
                    head.append(link_tag)

                    # Update the item content
                    item.set_content(str(soup).encode("utf-8"))

    def _mark_svo_in_soup(self, soup: BeautifulSoup, sentence_svos: dict) -> None:
        """
        Marks SVO structures in the BeautifulSoup object using CSS classes.

        Args:
            soup: The BeautifulSoup object to modify.
            sentence_svos: Dictionary mapping sentences to their SVO structures.
        """
        css_classes = {
            "subject": "svo-subject",
            "predicate": "svo-predicate",
            "object": "svo-object",
        }

        for sentence, svo_list in sentence_svos.items():
            for svo in svo_list:
                for component, css_class in css_classes.items():
                    if svo[component]:
                        text = svo[component]
                        for element in soup.find_all(string=True):
                            if text in element:
                                new_text = element.replace(
                                    text, f'<span class="{css_class}">{text}</span>'
                                )
                                element.replace_with(
                                    BeautifulSoup(new_text, "html.parser")
                                )

    def _mark_keywords_in_soup(self, soup: BeautifulSoup, keywords_data: dict) -> None:
        """
        Marks keywords in the BeautifulSoup object using CSS classes.

        Args:
            soup: The BeautifulSoup object to modify.
            keywords_data: Dictionary containing keywords with their importance levels.
        """
        # Map importance levels to CSS classes
        css_classes = {
            "super": "keyword-super",
            "required": "keyword-required",
            "important": "keyword-important",
        }

        # Extract keywords from the data structure
        for text, keywords_list in keywords_data.items():
            # Sort keywords by length (longer first) to avoid partial matches
            sorted_keywords = sorted(
                keywords_list, key=lambda x: len(x[0]), reverse=True
            )

            for keyword, score, importance in sorted_keywords:
                css_class = css_classes.get(importance, "keyword-important")

                # Find and replace the keyword in text nodes
                for element in soup.find_all(string=True):
                    if keyword in element:
                        # Use word boundaries to avoid partial matches
                        import re

                        pattern = r"\b" + re.escape(keyword) + r"\b"
                        new_text = re.sub(
                            pattern,
                            f'<span class="{css_class}">{keyword}</span>',
                            element,
                        )
                        if new_text != element:
                            element.replace_with(BeautifulSoup(new_text, "html.parser"))
                            break  # Move to next keyword after marking
