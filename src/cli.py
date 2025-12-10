#!/usr/bin/env python3
"""
Command-line interface for Bionic Reading EPUB processor.
"""

from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress

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


def generate_default_output_path(ctx, param, value):
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
    console = Console()

    # Display header
    header = f"[bold cyan]Bionic Reading EPUB Processor[/bold cyan]\n\n"
    header += f"Input: [green]{epub_path}[/green]\n"
    header += f"Output: [green]{output_path}[/green]"
    console.print(Panel(header, expand=False))

    console.print("Loading NLP model...", style="yellow")
    chinese_analyzer = ChineseAnalyzer()

    # Convert no_inline_css flag to inline_css parameter
    # no_inline_css=True means use external CSS (inline_css=False)
    # no_inline_css=False means use inline CSS (inline_css=True)
    inline_css = not no_inline_css
    parser = EpubParser(str(epub_path), inline_css=inline_css)
    doc_count = parser.get_document_count()

    with Progress(
        transient=True,
        redirect_stderr=False,
        redirect_stdout=False,
        console=console,
    ) as progress:
        task = progress.add_task("[b]Processing...[/b]", total=doc_count)

        parser.parse_chinese(chinese_analyzer, progress, task)

        # After processing, save the file
        console.print("Saving EPUB...", style="yellow")
        parser.save(str(output_path))

    console.print(f"[bold green]✓[/bold green] Processing complete!")
    console.print(
        f"✓ [bold]Output saved to:[/bold] [link=file://{output_path}]{output_path}[/link]"
    )


if __name__ == "__main__":
    cli()
