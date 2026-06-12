"""Persistence helpers for extraction outputs."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from research_platform.store.models import (
    Document,
    DocumentArtifact,
    DocumentExtraction,
    Fact,
    NarrativeExtract,
)


class ExtractionPersistenceRepository:
    """Repository for storing extraction outputs and derived records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_document(self, document_id) -> Document | None:
        return self.session.get(Document, _coerce_uuid(document_id))

    def get_artifact_by_path(self, file_path: str) -> DocumentArtifact | None:
        artifacts = self.session.execute(select(DocumentArtifact)).scalars().all()
        normalized_target = _normalize_file_path(file_path)
        for artifact in artifacts:
            if _normalize_file_path(artifact.file_path) == normalized_target:
                return artifact
        return None

    def add_artifact(self, artifact: DocumentArtifact) -> DocumentArtifact:
        self.session.add(artifact)
        self.session.flush()
        return artifact

    def get_extraction(
        self,
        *,
        document_id,
        artifact_id,
        extraction_type: str,
        extractor_name: str,
    ) -> DocumentExtraction | None:
        stmt = select(DocumentExtraction).where(
            DocumentExtraction.document_id == document_id,
            DocumentExtraction.artifact_id == artifact_id,
            DocumentExtraction.extraction_type == extraction_type,
            DocumentExtraction.extractor_name == extractor_name,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def add_extraction(self, extraction: DocumentExtraction) -> DocumentExtraction:
        self.session.add(extraction)
        self.session.flush()
        return extraction

    def replace_facts(self, *, extraction_id, facts: list[Fact]) -> None:
        self.session.execute(delete(Fact).where(Fact.extraction_id == extraction_id))
        self.session.flush()
        for fact in facts:
            self.session.add(fact)
        self.session.flush()

    def replace_narratives(self, *, extraction_id, narratives: list[NarrativeExtract]) -> None:
        self.session.execute(delete(NarrativeExtract).where(NarrativeExtract.extraction_id == extraction_id))
        self.session.flush()
        for narrative in narratives:
            self.session.add(narrative)
        self.session.flush()


def _normalize_file_path(file_path: str) -> str:
    return str(file_path).replace("\\", "/").lower()


def _coerce_uuid(value):
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))
