#!/usr/bin/env python3
"""
Command-line interface for Bionic Reading EPUB processor.
"""

from pathlib import Path

import click
from loguru import logger
from tqdm import tqdm

from src.analyzer import ChineseAnalyzer
from src.epub_parser import EpubParser


@click.command()
@click.option(
    "--input-path",
    "epub_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to the EPUB file to process.",
)
@click.option(
    "--output-path",
    "output_path",
    required=False,
    type=click.Path(path_type=Path),
    help="Path where the processed EPUB will be saved. Defaults to input directory with '_bio' suffix.",
)
def cli(epub_path: Path, output_path: Path | None = None):
    """Processes an EPUB file to apply bionic reading formatting to Chinese text."""

    output_path = output_path or epub_path.parent / f"{epub_path.stem}_bio.epub"
    if epub_path.suffix.lower() != ".epub":
        raise click.BadParameter(f"File must be an EPUB: {epub_path}")

    # Configure loguru
    logger.remove()
    # Log to file with DEBUG level
    logger.add("biocn.log", level="DEBUG", rotation="10 MB", compression="zip")
    # Print to console with INFO level (integrated with tqdm)
    logger.add(lambda msg: tqdm.write(msg, end=""), level="INFO", colorize=True)

    click.secho("\nBionic Reading EPUB Processor", fg="cyan", bold=True)
    click.echo(f"Input: {epub_path}")
    click.echo(f"Output: {output_path}\n")

    # Analyzer will log its loading status via loguru
    chinese_analyzer = ChineseAnalyzer()

    parser = EpubParser(str(epub_path))
    doc_count = parser.get_document_count()

    with tqdm(total=doc_count, desc="Processing", unit="doc") as pbar:
        parser.parse_chinese(chinese_analyzer, progress_callback=pbar.update)

        # After processing, save the file
        click.secho("\nSaving EPUB...", fg="yellow")
        parser.save(str(output_path))

    click.secho("\n✓ Processing complete!", fg="green", bold=True)
    click.echo(f"✓ Output saved to: {output_path}")


if __name__ == "__main__":
    cli()
