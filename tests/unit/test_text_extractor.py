from pathlib import Path
from unittest.mock import patch

import pytest

from research_platform.documents.text_extractor import TextExtractionError, extract_text


def mock_to_markdown(content: str):
    """Return a patch that makes pymupdf4llm.to_markdown return content."""
    return patch(
        "research_platform.documents.text_extractor.pymupdf4llm.to_markdown",
        return_value=content,
    )


# ---------------------------------------------------------------------------
# Routing and error handling
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(TextExtractionError, match="File not found"):
            extract_text(tmp_path / "missing.pdf")

    def test_unsupported_extension_raises(self, tmp_path):
        f = tmp_path / "doc.docx"
        f.write_bytes(b"data")
        with pytest.raises(TextExtractionError, match="Unsupported file type"):
            extract_text(f)

    def test_not_installed_raises(self, tmp_path):
        f = tmp_path / "report.pdf"
        f.write_bytes(b"%PDF fake")
        with patch("research_platform.documents.text_extractor.pymupdf4llm", None):
            with pytest.raises(TextExtractionError, match="pymupdf4llm is not installed"):
                extract_text(f)

    def test_pdf_calls_to_markdown(self, tmp_path):
        f = tmp_path / "report.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        with mock_to_markdown("# Annual Report\n\nRevenue: £70bn"):
            result = extract_text(f)
        assert "Annual Report" in result
        assert "£70bn" in result

    def test_html_calls_to_markdown(self, tmp_path):
        f = tmp_path / "rns.html"
        f.write_text("<p>Revenue up 11%.</p>", encoding="utf-8")
        with mock_to_markdown("Revenue up 11%."):
            result = extract_text(f)
        assert "Revenue up 11%" in result

    def test_htm_extension_accepted(self, tmp_path):
        f = tmp_path / "rns.htm"
        f.write_text("<p>Content</p>", encoding="utf-8")
        with mock_to_markdown("Content"):
            result = extract_text(f)
        assert "Content" in result

    def test_max_chars_truncates(self, tmp_path):
        f = tmp_path / "long.pdf"
        f.write_bytes(b"%PDF fake")
        long_text = "x" * 5000
        with mock_to_markdown(long_text):
            result = extract_text(f, max_chars=100)
        assert len(result) <= 105
        assert result.endswith("…")

    def test_max_chars_not_applied_when_short(self, tmp_path):
        f = tmp_path / "short.pdf"
        f.write_bytes(b"%PDF fake")
        with mock_to_markdown("Short text."):
            result = extract_text(f, max_chars=1000)
        assert result == "Short text."

    def test_pymupdf_error_raises_extraction_error(self, tmp_path):
        f = tmp_path / "bad.pdf"
        f.write_bytes(b"not a pdf")
        with patch(
            "research_platform.documents.text_extractor.pymupdf4llm.to_markdown",
            side_effect=RuntimeError("cannot open"),
        ):
            with pytest.raises(TextExtractionError, match="pymupdf4llm failed"):
                extract_text(f)

    def test_to_markdown_called_with_string_path(self, tmp_path):
        f = tmp_path / "report.pdf"
        f.write_bytes(b"%PDF fake")
        with patch(
            "research_platform.documents.text_extractor.pymupdf4llm.to_markdown",
            return_value="text",
        ) as mock_fn:
            extract_text(f)
        mock_fn.assert_called_once_with(str(f))
