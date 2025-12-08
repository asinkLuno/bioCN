import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub


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

    def parse_chinese(self, chinese_analyzer: "ChineseAnalyzer") -> None:
        """
        Extracts and analyzes Chinese text from the EPUB file with SVO markup.
        Marks subjects (blue bold), predicates (underline), objects (green bold).
        Modifies the book object in-place.

        Args:
            chinese_analyzer: The Chinese analyzer for SVO extraction.
        """
        for item in list(self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT)):
            print("Analyzing new item...")

            soup = BeautifulSoup(item.get_content(), "html.parser")

            # Get all text for analysis
            text = soup.get_text()
            sentence_svos = chinese_analyzer.analyze(text)

            # Mark SVO structures in the HTML
            self._mark_svo_in_soup(soup, sentence_svos)

            # Update the item content in the book
            item.set_content(str(soup).encode("utf-8"))

    def save(self, output_path: str) -> None:
        """
        Saves the modified EPUB to the specified path.

        Args:
            output_path: The path where the modified EPUB will be saved.
        """
        epub.write_epub(output_path, self.book, {})

    def _mark_svo_in_soup(self, soup: BeautifulSoup, sentence_svos: dict) -> None:
        """
        Marks SVO structures in the BeautifulSoup object.

        Args:
            soup: The BeautifulSoup object to modify.
            sentence_svos: Dictionary mapping sentences to their SVO structures.
        """
        for sentence, svo_list in sentence_svos.items():
            for svo in svo_list:
                self._mark_svo_components(soup, svo)

    def _mark_svo_components(self, soup: BeautifulSoup, svo: dict) -> None:
        """
        Marks individual SVO components in the HTML.

        Args:
            soup: The BeautifulSoup object to modify.
            svo: Dictionary containing subject, predicate, object.
        """
        # Mark subject (blue bold)
        if svo["subject"]:
            self._mark_text(
                soup,
                svo["subject"],
                '<span style="color: blue; font-weight: bold;">',
                "</span>",
            )

        # Mark predicate (underline)
        if svo["predicate"]:
            self._mark_text(
                soup,
                svo["predicate"],
                '<span style="text-decoration: underline;">',
                "</span>",
            )

        # Mark object (green bold)
        if svo["object"]:
            self._mark_text(
                soup,
                svo["object"],
                '<span style="color: green; font-weight: bold;">',
                "</span>",
            )

    def _mark_text(
        self, soup: BeautifulSoup, text: str, start_tag: str, end_tag: str
    ) -> None:
        """
        Marks specific text in the BeautifulSoup with the given tags.

        Args:
            soup: The BeautifulSoup object to modify.
            text: The text to mark.
            start_tag: The opening tag to wrap around the text.
            end_tag: The closing tag to wrap around the text.
        """
        if not text:
            return

        # Find all text nodes and replace the text if it matches
        for element in soup.find_all(string=True):
            if text in element:
                new_text = element.replace(text, f"{start_tag}{text}{end_tag}")
                element.replace_with(BeautifulSoup(new_text, "html.parser"))
