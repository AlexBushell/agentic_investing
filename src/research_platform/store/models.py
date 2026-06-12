"""Canonical persistence models for the company data store."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy import JSON as JSONType
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import Uuid


class Base(DeclarativeBase):
    """Base class for store persistence models."""


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Company(Base):
    """Canonical company identity."""

    __tablename__ = "companies"

    company_id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Identifier(Base):
    """Identifier history for a company."""

    __tablename__ = "identifiers"

    identifier_id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.company_id"), nullable=False, index=True
    )
    id_type: Mapped[str] = mapped_column(String(32), nullable=False)
    id_value: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str | None] = mapped_column(String(64))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_identifiers_type_value", "id_type", "id_value", unique=True),
    )


class Listing(Base):
    """Exchange-specific listing or instrument record."""

    __tablename__ = "listings"

    listing_id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.company_id"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange_code: Mapped[str | None] = mapped_column(String(32))
    security_type: Mapped[str | None] = mapped_column(String(128))
    market_sector: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str | None] = mapped_column(String(16))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Document(Base):
    """Business-level document registry record."""

    __tablename__ = "documents"

    document_id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.company_id"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    document_role: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    publication_date: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_reference: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DocumentArtifact(Base):
    """Physical file or raw artifact associated with a document."""

    __tablename__ = "document_artifacts"

    artifact_id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.document_id"), nullable=False, index=True
    )
    artifact_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    format: Mapped[str | None] = mapped_column(String(32))
    size_bytes: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DocumentExtraction(Base):
    """Extraction output associated with a document artifact."""

    __tablename__ = "document_extractions"

    extraction_id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.document_id"), nullable=False, index=True
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("document_artifacts.artifact_id")
    )
    extraction_type: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_name: Mapped[str] = mapped_column(String(128), nullable=False)
    extractor_version: Mapped[str | None] = mapped_column(String(64))
    payload_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Fact(Base):
    """Normalized structured company fact."""

    __tablename__ = "facts"

    fact_id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.company_id"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.document_id"), nullable=False, index=True
    )
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_extractions.extraction_id"), nullable=False, index=True
    )
    concept: Mapped[str] = mapped_column(String(255), nullable=False)
    namespace: Mapped[str | None] = mapped_column(String(128))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    instant_date: Mapped[date | None] = mapped_column(Date)
    unit: Mapped[str | None] = mapped_column(String(32))
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    value_text: Mapped[str | None] = mapped_column(Text)
    dimensions_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONType)
    source_confidence: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NarrativeExtract(Base):
    """Narrative passage with explicit provenance."""

    __tablename__ = "narrative_extracts"

    narrative_id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.company_id"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.document_id"), nullable=False, index=True
    )
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_extractions.extraction_id"), nullable=False, index=True
    )
    section_name: Mapped[str | None] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int | None]
    source_confidence: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DocumentChunk(Base):
    """Chunked narrative passage for retrieval."""

    __tablename__ = "document_chunks"

    chunk_id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.company_id"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.document_id"), nullable=False, index=True
    )
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("document_extractions.extraction_id"), index=True
    )
    narrative_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("narrative_extracts.narrative_id"), index=True
    )
    section_name: Mapped[str | None] = mapped_column(String(255))
    chunk_index: Mapped[int] = mapped_column(nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int | None]
    source_confidence: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MarketSnapshot(Base):
    """Point-in-time market snapshot."""

    __tablename__ = "market_snapshots"

    snapshot_id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.company_id"), nullable=False, index=True
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("listings.listing_id")
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str | None] = mapped_column(String(16))
    price: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    enterprise_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    shares_outstanding: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    week_52_high: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    week_52_low: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    payload_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IngestionRun(Base):
    """Operational audit trail for gather/store work."""

    __tablename__ = "ingestion_runs"

    ingestion_run_id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("companies.company_id"), index=True
    )
    run_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


STORE_TABLES: tuple[str, ...] = tuple(Base.metadata.tables.keys())
