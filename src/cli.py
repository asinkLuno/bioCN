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


def validate_epub_path(ctx, _param, value):
    """Validate EPUB file path - no silent failures."""
    path = Path(value)

    if not path.exists():
        raise click.BadParameter(f"EPUB file does not exist: {value}")

    if not path.is_file():
        raise click.BadParameter(f"Path is not a file: {value}")

    if path.suffix.lower() != ".epub":
        raise click.BadParameter(f"File must be an EPUB: {value}")

    return path


def generate_default_output_path(ctx, _param, value):
    """Generate default output path based on input path if not provided."""
    if value is not None:
        return Path(value)

    # Get the input path from the context
    epub_path = ctx.params.get("epub_path")
    if epub_path is None:
        raise click.BadParameter(
            "Cannot generate default output path without input path"
        )

    # Generate default: same directory, same name with _bio suffix
    stem = epub_path.stem
    default_output = epub_path.parent / f"{stem}_bio.epub"
    return default_output


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
    required=False,
    type=click.Path(),
    callback=generate_default_output_path,
    help="Path where the processed EPUB will be saved. Defaults to input directory with '_bio' suffix.",
)
@click.option(
    "--no-inline-css",
    "no_inline_css",
    is_flag=True,
    default=False,
    help="Use external CSS stylesheet instead of inline styles. Default is False (inline CSS).",
)
def cli(epub_path: Path, output_path: Path, no_inline_css: bool):
    """Processes an EPUB file to apply bionic reading formatting to Chinese text."""

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

    # Convert no_inline_css flag to inline_css parameter
    inline_css = not no_inline_css
    parser = EpubParser(str(epub_path), inline_css=inline_css)
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
