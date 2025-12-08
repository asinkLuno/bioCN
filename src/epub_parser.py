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

    def _mark_svo_in_soup(self, soup: BeautifulSoup, sentence_svos: dict) -> None:
        """
        Marks SVO structures in the BeautifulSoup object.

        Args:
            soup: The BeautifulSoup object to modify.
            sentence_svos: Dictionary mapping sentences to their SVO structures.
        """
        styles = {
            "subject": '<span style="color: blue; font-weight: bold;">',
            "predicate": '<span style="color: red; font-weight: bold;">',
            "object": '<span style="color: green; font-weight: bold;">',
        }

        for sentence, svo_list in sentence_svos.items():
            for svo in svo_list:
                for component, style_tag in styles.items():
                    if svo[component]:
                        text = svo[component]
                        for element in soup.find_all(string=True):
                            if text in element:
                                new_text = element.replace(
                                    text, f"{style_tag}{text}</span>"
                                )
                                element.replace_with(
                                    BeautifulSoup(new_text, "html.parser")
                                )
