from __future__ import annotations

from pathlib import Path
from typing import Optional

from research_platform.core.logging import get_logger

logger = get_logger(__name__)

_SUPPORTED = {".pdf", ".html", ".htm"}

try:
    import pymupdf4llm
except ImportError:
    pymupdf4llm = None  # type: ignore


class TextExtractionError(RuntimeError):
    """Raised when text cannot be extracted from a document."""


def extract_text(file_path: Path, max_chars: Optional[int] = None) -> str:
    """Extract readable markdown text from a PDF or HTML file using pymupdf4llm.

    Args:
        file_path: Path to the document.
        max_chars: If set, truncate output to this many characters.

    Returns:
        Extracted text as markdown string.
    """
    if not file_path.exists():
        raise TextExtractionError(f"File not found: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix not in _SUPPORTED:
        raise TextExtractionError(
            f"Unsupported file type {suffix!r}. Supported: {', '.join(sorted(_SUPPORTED))}"
        )

    if pymupdf4llm is None:
        raise TextExtractionError(
            "pymupdf4llm is not installed. Run: pip install pymupdf4llm"
        )

    try:
        text = pymupdf4llm.to_markdown(str(file_path))
    except Exception as exc:
        raise TextExtractionError(
            f"pymupdf4llm failed on {file_path.name}: {exc}"
        ) from exc

    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n…"

    logger.info("Extracted %d chars from %s", len(text), file_path.name)
    return text
