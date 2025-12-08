#!/usr/bin/env python3
"""
Command-line interface for Bionic Reading EPUB processor.
"""

from pathlib import Path

import click
from rich.console import Console

from src.analyzer import ChineseAnalyzer
from src.epub_parser import EpubParser


def validate_epub_path(ctx, param, value):
    """Validate EPUB file path - no silent failures."""
    path = Path(value)

    if not path.exists():
        raise click.BadParameter(f"EPUB file does not exist: {value}")

    if not path.is_file():
        raise click.BadParameter(f"Path is not a file: {value}")

    if path.suffix.lower() != ".epub":
        raise click.BadParameter(f"File must be an EPUB: {value}")

    return path


@click.command()
@click.option(
    "--input-path",
    "epub_path",
    required=True,
    type=click.Path(exists=False),
    callback=validate_epub_path,
    help="Path to the EPUB file to process.",
)
@click.option(
    "--output-path",
    "output_path",
    required=True,
    type=click.Path(),
    help="Path where the processed EPUB will be saved.",
)
def cli(epub_path: Path, output_path: Path):
    console = Console()
    console.print(f"Processing EPUB: {epub_path}")

    parser = EpubParser(str(epub_path))
    chinese_analyzer = ChineseAnalyzer()

    console.print("Analyzing Chinese text and marking SVO structures...")
    parser.parse_chinese(chinese_analyzer)

    console.print(f"Saving processed EPUB to: {output_path}")
    parser.save(str(output_path))

    console.print("[green]✓[/green] EPUB processing completed!")


if __name__ == "__main__":
    cli()
