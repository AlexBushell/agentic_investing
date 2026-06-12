"""Persistence helpers for document chunking."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from research_platform.store.models import Document, DocumentChunk, NarrativeExtract


class ChunkingRepository:
    """Repository for narrative chunk creation and retrieval."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_document(self, document_id):
        return self.session.get(Document, _coerce_uuid(document_id))

    def get_narratives_for_document(self, document_id) -> list[NarrativeExtract]:
        document_pk = _coerce_uuid(document_id)
        stmt = (
            select(NarrativeExtract)
            .where(NarrativeExtract.document_id == document_pk)
            .order_by(NarrativeExtract.section_name, NarrativeExtract.narrative_id)
        )
        return self.session.execute(stmt).scalars().all()

    def replace_chunks_for_document(self, *, document_id, chunks: list[DocumentChunk]) -> None:
        document_pk = _coerce_uuid(document_id)
        self.session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_pk))
        self.session.flush()
        for chunk in chunks:
            self.session.add(chunk)
        self.session.flush()


def _coerce_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return value
