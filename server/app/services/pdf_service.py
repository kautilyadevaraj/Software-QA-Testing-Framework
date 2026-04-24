"""PDF text extraction — page-by-page using PyMuPDF (no OCR)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class PageText:
    """Text extracted from a single PDF page."""
    page_number: int   # 1-based
    text: str


def extract_text_from_pdf(file_path: str) -> list[PageText]:
    """Open a PDF and extract text from every page.

    Returns a list of PageText objects ordered by page number.
    Blank pages are included (with empty text) to preserve page numbering.
    """
    pages: list[PageText] = []
    doc = fitz.open(file_path)
    try:
        for page_index in range(len(doc)):
            page = doc.load_page(page_index)
            text = page.get_text("text")  # plain-text extraction, no OCR
            pages.append(PageText(page_number=page_index + 1, text=text))
        logger.info("Extracted %d pages from '%s'.", len(pages), file_path)
    finally:
        doc.close()
    return pages


def save_extracted_text(
    project_id: str,
    file_id: str,
    pages: list[PageText],
) -> Path:
    """Write extracted text to disk under the configured extracted_text_dir.

    Layout:  extracted/{project_id}/{file_id}.txt
    Pages are separated by a marker line: --- PAGE N ---
    Returns the absolute path of the written file.
    """
    settings = get_settings()
    out_dir = Path(settings.extracted_text_dir) / project_id
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{file_id}.txt"

    with open(out_path, "w", encoding="utf-8") as fh:
        for page in pages:
            fh.write(f"--- PAGE {page.page_number} ---\n")
            fh.write(page.text)
            fh.write("\n\n")

    logger.info("Saved extracted text → %s", out_path)
    return out_path.resolve()
