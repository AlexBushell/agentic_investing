"""Persistence helpers for document and artifact ingestion."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from research_platform.store.models import Company, Document, DocumentArtifact


class DocumentIngestionRepository:
    """Repository for document registry and artifact persistence."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_company_by_id(self, company_id) -> Company | None:
        return self.session.get(Company, company_id)

    def get_company_by_name(self, name: str) -> Company | None:
        stmt = select(Company).where(Company.name == name)
        return self.session.execute(stmt).scalar_one_or_none()

    def add_company(self, company: Company) -> Company:
        self.session.add(company)
        self.session.flush()
        return company

    def get_document(
        self,
        *,
        company_id,
        source: str,
        document_role: str,
        title: str | None,
        publication_date: date | None,
        source_reference: str | None = None,
    ) -> Document | None:
        stmt = select(Document).where(
            Document.company_id == company_id,
            Document.source == source,
            Document.document_role == document_role,
        )
        if source_reference is not None:
            stmt = stmt.where(Document.source_reference == source_reference)
        else:
            stmt = stmt.where(
                Document.title == title,
                Document.publication_date == publication_date,
            )
        return self.session.execute(stmt).scalar_one_or_none()

    def add_document(self, document: Document) -> Document:
        self.session.add(document)
        self.session.flush()
        return document

    def get_artifact(
        self,
        *,
        document_id,
        artifact_kind: str,
        file_path: str,
    ) -> DocumentArtifact | None:
        stmt = select(DocumentArtifact).where(
            DocumentArtifact.document_id == document_id,
            DocumentArtifact.artifact_kind == artifact_kind,
            DocumentArtifact.file_path == file_path,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def add_artifact(self, artifact: DocumentArtifact) -> DocumentArtifact:
        self.session.add(artifact)
        self.session.flush()
        return artifact
