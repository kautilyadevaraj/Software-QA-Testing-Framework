"""Unit tests for app.services.pdf_service — PDF text extraction and file saving."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.pdf_service import PageText, extract_text_from_pdf, save_extracted_text


# ---------------------------------------------------------------------------
# We mock fitz (PyMuPDF) to avoid needing real PDF files in CI.
# ---------------------------------------------------------------------------

def _mock_fitz_doc(pages: list[str]):
    """Create a mock fitz.Document with given page texts."""
    doc = MagicMock()
    doc.__len__ = MagicMock(return_value=len(pages))

    def _load_page(idx):
        page = MagicMock()
        page.get_text.return_value = pages[idx]
        return page

    doc.load_page = _load_page
    doc.close = MagicMock()

    # Make it work as a range-able object
    def _range_len():
        return len(pages)

    return doc


class TestExtractTextFromPDF:
    @patch("app.services.pdf_service.fitz")
    def test_basic_extraction(self, mock_fitz):
        page_texts = ["Page one content", "Page two content", "Page three"]
        mock_fitz.open.return_value = _mock_fitz_doc(page_texts)

        result = extract_text_from_pdf("/fake/path.pdf")

        assert len(result) == 3
        assert result[0].page_number == 1
        assert result[0].text == "Page one content"
        assert result[2].page_number == 3
        assert result[2].text == "Page three"

    @patch("app.services.pdf_service.fitz")
    def test_blank_page_included(self, mock_fitz):
        page_texts = ["Content", "", "More content"]
        mock_fitz.open.return_value = _mock_fitz_doc(page_texts)

        result = extract_text_from_pdf("/fake/path.pdf")
        assert len(result) == 3
        assert result[1].text == ""
        assert result[1].page_number == 2

    @patch("app.services.pdf_service.fitz")
    def test_single_page(self, mock_fitz):
        mock_fitz.open.return_value = _mock_fitz_doc(["Only page"])

        result = extract_text_from_pdf("/fake/path.pdf")
        assert len(result) == 1


class TestSaveExtractedText:
    def test_writes_file_with_page_markers(self, tmp_path, monkeypatch):
        # Override the settings to use tmp_path
        from app.core.config import get_settings
        settings = get_settings()
        monkeypatch.setattr(settings, "extracted_text_dir", str(tmp_path))

        pages = [
            PageText(page_number=1, text="First page text"),
            PageText(page_number=2, text="Second page text"),
        ]

        result_path = save_extracted_text("proj-1", "file-1", pages)

        assert result_path.exists()
        content = result_path.read_text(encoding="utf-8")
        assert "--- PAGE 1 ---" in content
        assert "First page text" in content
        assert "--- PAGE 2 ---" in content
        assert "Second page text" in content

    def test_creates_directory_structure(self, tmp_path, monkeypatch):
        from app.core.config import get_settings
        settings = get_settings()
        monkeypatch.setattr(settings, "extracted_text_dir", str(tmp_path))

        pages = [PageText(page_number=1, text="text")]
        save_extracted_text("new-proj", "new-file", pages)

        proj_dir = tmp_path / "new-proj"
        assert proj_dir.is_dir()
        assert (proj_dir / "new-file.txt").exists()
