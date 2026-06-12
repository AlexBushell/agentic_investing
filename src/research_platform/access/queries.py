"""Store-backed company context queries."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Select, desc, or_, select
from sqlalchemy.orm import Session

from research_platform.access.company_context import CompanyContextStore
from research_platform.access.dto import (
    ArtifactRecord,
    ArtifactWithProvenance,
    CompanyContextBundle,
    CompanyRecord,
    DocumentRecord,
    FactSet,
    IdentifierRecord,
    ListingRecord,
    NarrativeExtract,
    PassageRecord,
)
from research_platform.store.models import (
    Company,
    Document,
    DocumentArtifact,
    DocumentChunk,
    Fact,
    Identifier,
    Listing,
    NarrativeExtract as NarrativeExtractModel,
)


class SQLCompanyContextStore(CompanyContextStore):
    """SQLAlchemy-backed company context access layer."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_company(self, company_ref: str) -> CompanyRecord:
        company = self._resolve_company(company_ref)
        if company is None:
            raise ValueError(f"Company not found: {company_ref}")
        return CompanyRecord(
            company_id=str(company.company_id),
            name=company.name,
            legal_name=company.legal_name,
            country=company.country,
        )

    def get_identifiers(self, company_id: str) -> list[IdentifierRecord]:
        company_pk = _coerce_uuid(company_id)
        stmt = (
            select(Identifier)
            .where(Identifier.company_id == company_pk)
            .order_by(desc(Identifier.is_primary), Identifier.id_type, Identifier.id_value)
        )
        identifiers = self.session.execute(stmt).scalars().all()
        return [
            IdentifierRecord(
                identifier_id=str(item.identifier_id),
                id_type=item.id_type,
                id_value=item.id_value,
                source=item.source,
                is_primary=item.is_primary,
            )
            for item in identifiers
        ]

    def get_primary_listing(self, company_id: str) -> ListingRecord | None:
        company_pk = _coerce_uuid(company_id)
        stmt = (
            select(Listing)
            .where(Listing.company_id == company_pk)
            .order_by(desc(Listing.is_primary), Listing.exchange_code, Listing.ticker)
        )
        listing = self.session.execute(stmt).scalars().first()
        if listing is None:
            return None
        return ListingRecord(
            listing_id=str(listing.listing_id),
            ticker=listing.ticker,
            exchange_code=listing.exchange_code,
            security_type=listing.security_type,
            market_sector=listing.market_sector,
            currency=listing.currency,
            is_primary=listing.is_primary,
        )

    def get_latest_documents(self, company_id: str) -> list[DocumentRecord]:
        company_pk = _coerce_uuid(company_id)
        stmt = (
            select(Document)
            .where(Document.company_id == company_pk)
            .order_by(desc(Document.publication_date), desc(Document.created_at))
        )
        documents = self.session.execute(stmt).scalars().all()
        return [self._to_document_record(document) for document in documents]

    def get_document(self, document_id: str) -> DocumentRecord:
        document = self.session.get(Document, _coerce_uuid(document_id))
        if document is None:
            raise ValueError(f"Document not found: {document_id}")
        return self._to_document_record(document)

    def get_document_artifacts(self, document_id: str) -> list[ArtifactRecord]:
        document_pk = _coerce_uuid(document_id)
        stmt = (
            select(DocumentArtifact)
            .where(DocumentArtifact.document_id == document_pk)
            .order_by(DocumentArtifact.artifact_kind, DocumentArtifact.file_path)
        )
        artifacts = self.session.execute(stmt).scalars().all()
        return [
            ArtifactRecord(
                artifact_id=str(item.artifact_id),
                file_path=item.file_path,
                artifact_kind=item.artifact_kind,
                file_hash=item.file_hash,
                format=item.format,
                size_bytes=item.size_bytes,
            )
            for item in artifacts
        ]

    def list_artifacts_for_company(self, company_id: str) -> list[ArtifactWithProvenance]:
        company_pk = _coerce_uuid(company_id)
        stmt = (
            select(DocumentArtifact, Document, Company)
            .join(Document, Document.document_id == DocumentArtifact.document_id)
            .join(Company, Company.company_id == Document.company_id)
            .where(Document.company_id == company_pk)
            .order_by(desc(Document.publication_date), DocumentArtifact.artifact_kind, DocumentArtifact.file_path)
        )
        rows = self.session.execute(stmt).all()
        return [
            ArtifactWithProvenance(
                artifact=self._to_artifact_record(artifact),
                document=self._to_document_record(document),
                company=self._to_company_record(company),
            )
            for artifact, document, company in rows
        ]

    def get_artifact(self, artifact_id: str) -> ArtifactWithProvenance:
        artifact_pk = _coerce_uuid(artifact_id)
        stmt = (
            select(DocumentArtifact, Document, Company)
            .join(Document, Document.document_id == DocumentArtifact.document_id)
            .join(Company, Company.company_id == Document.company_id)
            .where(DocumentArtifact.artifact_id == artifact_pk)
        )
        row = self.session.execute(stmt).first()
        if row is None:
            raise ValueError(f"Artifact not found: {artifact_id}")
        artifact, document, company = row
        return ArtifactWithProvenance(
            artifact=self._to_artifact_record(artifact),
            document=self._to_document_record(document),
            company=self._to_company_record(company),
        )

    def get_fact_set(
        self,
        company_id: str,
        *,
        document_role: str | None = None,
    ) -> FactSet:
        company_pk = _coerce_uuid(company_id)
        stmt: Select[tuple[Fact]] = select(Fact).where(Fact.company_id == company_pk)
        if document_role is not None:
            stmt = stmt.join(Document, Document.document_id == Fact.document_id).where(
                Document.document_role == document_role
            )
        stmt = stmt.order_by(desc(Fact.period_end), desc(Fact.instant_date), Fact.concept)

        facts = self.session.execute(stmt).scalars().all()
        payload = []
        for fact in facts:
            payload.append(
                {
                    "fact_id": str(fact.fact_id),
                    "concept": fact.concept,
                    "namespace": fact.namespace,
                    "period_start": fact.period_start.isoformat() if fact.period_start else None,
                    "period_end": fact.period_end.isoformat() if fact.period_end else None,
                    "instant_date": fact.instant_date.isoformat() if fact.instant_date else None,
                    "unit": fact.unit,
                    "value_numeric": _decimal_to_float(fact.value_numeric),
                    "value_text": fact.value_text,
                    "dimensions": fact.dimensions_json,
                    "source_confidence": fact.source_confidence,
                    "document_id": str(fact.document_id),
                    "extraction_id": str(fact.extraction_id),
                }
            )
        return FactSet(facts=payload)

    def get_narrative_extracts(
        self,
        company_id: str,
        *,
        document_role: str | None = None,
    ) -> list[NarrativeExtract]:
        company_pk = _coerce_uuid(company_id)
        stmt: Select[tuple[NarrativeExtractModel]] = select(NarrativeExtractModel).where(
            NarrativeExtractModel.company_id == company_pk
        )
        if document_role is not None:
            stmt = stmt.join(Document, Document.document_id == NarrativeExtractModel.document_id).where(
                Document.document_role == document_role
            )
        stmt = stmt.order_by(desc(NarrativeExtractModel.char_count), NarrativeExtractModel.section_name)

        items = self.session.execute(stmt).scalars().all()
        return [
            NarrativeExtract(
                narrative_id=str(item.narrative_id),
                text=item.text,
                section_name=item.section_name,
            )
            for item in items
        ]

    def search_passages(
        self,
        company_id: str,
        *,
        query: str,
        document_role: str | None = None,
        limit: int = 20,
    ) -> list[PassageRecord]:
        company_pk = _coerce_uuid(company_id)
        stmt: Select[tuple[DocumentChunk]] = select(DocumentChunk).where(
            DocumentChunk.company_id == company_pk,
            DocumentChunk.chunk_text.ilike(f"%{query}%"),
        )
        if document_role is not None:
            stmt = stmt.join(Document, Document.document_id == DocumentChunk.document_id).where(
                Document.document_role == document_role
            )
        stmt = stmt.order_by(desc(DocumentChunk.char_count), DocumentChunk.chunk_index).limit(limit)
        items = self.session.execute(stmt).scalars().all()
        return [
            PassageRecord(
                chunk_id=str(item.chunk_id),
                document_id=str(item.document_id),
                section_name=item.section_name,
                chunk_index=item.chunk_index,
                text=item.chunk_text,
                char_count=item.char_count,
                source_confidence=item.source_confidence,
            )
            for item in items
        ]

    def build_company_context(self, company_ref: str) -> CompanyContextBundle:
        company = self.get_company(company_ref)
        return CompanyContextBundle(
            company=company,
            identifiers=self.get_identifiers(company.company_id),
            listing=self.get_primary_listing(company.company_id),
            documents=self.get_latest_documents(company.company_id),
            facts=self.get_fact_set(company.company_id),
            narratives=self.get_narrative_extracts(company.company_id),
        )

    def _resolve_company(self, company_ref: str) -> Company | None:
        company_pk = _try_coerce_uuid(company_ref)
        if company_pk is not None:
            company = self.session.get(Company, company_pk)
            if company is not None:
                return company

        stmt = (
            select(Company)
            .outerjoin(Identifier, Identifier.company_id == Company.company_id)
            .where(
                or_(
                    Company.name == company_ref,
                    Company.legal_name == company_ref,
                    Identifier.id_value == company_ref,
                )
            )
        )
        return self.session.execute(stmt).scalars().first()

    @staticmethod
    def _to_company_record(company: Company) -> CompanyRecord:
        return CompanyRecord(
            company_id=str(company.company_id),
            name=company.name,
            legal_name=company.legal_name,
            country=company.country,
        )

    @staticmethod
    def _to_document_record(document: Document) -> DocumentRecord:
        return DocumentRecord(
            document_id=str(document.document_id),
            document_role=document.document_role,
            title=document.title,
            source=document.source,
            publication_date=document.publication_date.isoformat() if document.publication_date else None,
            period_end=document.period_end.isoformat() if document.period_end else None,
            source_url=document.source_url,
            source_reference=document.source_reference,
        )

    @staticmethod
    def _to_artifact_record(artifact: DocumentArtifact) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=str(artifact.artifact_id),
            file_path=artifact.file_path,
            artifact_kind=artifact.artifact_kind,
            file_hash=artifact.file_hash,
            format=artifact.format,
            size_bytes=artifact.size_bytes,
        )


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _coerce_uuid(value: str):
    parsed = _try_coerce_uuid(value)
    return parsed if parsed is not None else value


def _try_coerce_uuid(value: str):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None
