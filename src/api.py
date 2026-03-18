"""
FastAPI application for Bionic Reading EPUB processor.
"""

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from src.analyzer import ChineseAnalyzer
from src.epub_parser import EpubParser

app = FastAPI(
    title="biocn API", description="Chinese bionic reading EPUB processor with HanLP"
)

# Global analyzer instance (lazy loaded)
_analyzer: ChineseAnalyzer | None = None


def get_analyzer() -> ChineseAnalyzer:
    """Get or create the global ChineseAnalyzer instance."""
    global _analyzer
    if _analyzer is None:
        _analyzer = ChineseAnalyzer()
    return _analyzer


@app.post("/process")
async def process_epub(
    file: UploadFile = File(..., description="EPUB file to process"),
    inline_css: bool = True,
) -> FileResponse:
    """
    Process an EPUB file with bionic reading formatting.

    Returns the processed EPUB file.
    """
    if not file.filename.lower().endswith(".epub"):
        raise HTTPException(status_code=400, detail="File must be an EPUB")

    # Save uploaded file to temp location
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp_input:
        tmp_input.write(await file.read())
        input_path = Path(tmp_input.name)

    try:
        # Process the EPUB
        analyzer = get_analyzer()
        parser = EpubParser(str(input_path), inline_css=inline_css)
        parser.parse_chinese(analyzer)

        # Save to temp output
        output_path = input_path.with_suffix(".bio.epub")
        parser.save(str(output_path))

        return FileResponse(
            path=output_path,
            media_type="application/epub+zip",
            filename=f"bio_{file.filename}",
            background=None,  # Don't delete file immediately
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cleanup input temp file
        input_path.unlink(missing_ok=True)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
