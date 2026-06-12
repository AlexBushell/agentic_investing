"""Services for chunking stored narrative extracts into retrieval passages."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from research_platform.store.models import DocumentChunk
from research_platform.store.repositories.chunking import ChunkingRepository


@dataclass(slots=True)
class ChunkingResult:
    """Summary of chunking work for one document."""

    document_id: str
    chunk_count: int


class NarrativeChunkingService:
    """Create retrieval chunks from stored narrative extracts."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = ChunkingRepository(session)

    def chunk_document(
        self,
        *,
        document_id: str,
        max_chars: int = 1200,
        overlap_chars: int = 150,
    ) -> ChunkingResult:
        document = self.repo.get_document(document_id)
        if document is None:
            raise ValueError(f"Document not found: {document_id}")

        narratives = self.repo.get_narratives_for_document(document.document_id)
        chunks: list[DocumentChunk] = []
        chunk_index = 0

        for narrative in narratives:
            for text_chunk in _chunk_text(narrative.text, max_chars=max_chars, overlap_chars=overlap_chars):
                chunks.append(
                    DocumentChunk(
                        company_id=document.company_id,
                        document_id=document.document_id,
                        extraction_id=narrative.extraction_id,
                        narrative_id=narrative.narrative_id,
                        section_name=narrative.section_name,
                        chunk_index=chunk_index,
                        chunk_text=text_chunk,
                        char_count=len(text_chunk),
                        source_confidence=narrative.source_confidence,
                    )
                )
                chunk_index += 1

        self.repo.replace_chunks_for_document(document_id=document.document_id, chunks=chunks)
        return ChunkingResult(document_id=str(document.document_id), chunk_count=len(chunks))


def _chunk_text(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
    if not paragraphs:
        paragraphs = [cleaned]

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
            overlap = current[-overlap_chars:] if overlap_chars > 0 else ""
            current = f"{overlap}\n\n{paragraph}".strip() if overlap else paragraph
            if len(current) <= max_chars:
                continue

        chunks.extend(_split_long_paragraph(paragraph, max_chars=max_chars, overlap_chars=overlap_chars))
        current = ""

    if current:
        chunks.append(current)

    return chunks


def _split_long_paragraph(paragraph: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    if len(sentences) <= 1:
        return [paragraph[i:i + max_chars] for i in range(0, len(paragraph), max_chars)]

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            overlap = current[-overlap_chars:] if overlap_chars > 0 else ""
            current = f"{overlap} {sentence}".strip() if overlap else sentence
        else:
            chunks.append(sentence[:max_chars])
            current = sentence[max_chars:].strip()
    if current:
        chunks.append(current)
    return chunks
