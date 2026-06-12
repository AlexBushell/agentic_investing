"""Lightweight data transfer objects for company context access."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CompanyRecord:
    """Minimal company identity record returned by the access layer."""

    company_id: str
    name: str
    legal_name: str | None = None
    country: str | None = None


@dataclass(slots=True)
class ListingRecord:
    """Minimal listing record returned by the access layer."""

    listing_id: str
    ticker: str
    exchange_code: str | None = None
    security_type: str | None = None
    market_sector: str | None = None
    currency: str | None = None
    is_primary: bool = False


@dataclass(slots=True)
class IdentifierRecord:
    """Identifier record returned by the access layer."""

    identifier_id: str
    id_type: str
    id_value: str
    source: str | None = None
    is_primary: bool = False


@dataclass(slots=True)
class DocumentRecord:
    """Minimal document registry record returned by the access layer."""

    document_id: str
    document_role: str
    title: str | None = None
    source: str | None = None
    publication_date: str | None = None
    period_end: str | None = None
    source_url: str | None = None
    source_reference: str | None = None


@dataclass(slots=True)
class ArtifactRecord:
    """Minimal file artifact record returned by the access layer."""

    artifact_id: str
    file_path: str
    artifact_kind: str
    file_hash: str | None = None
    format: str | None = None
    size_bytes: int | None = None


@dataclass(slots=True)
class ArtifactWithProvenance:
    """Artifact together with its parent document and company provenance."""

    artifact: ArtifactRecord
    document: DocumentRecord
    company: CompanyRecord


@dataclass(slots=True)
class FactSet:
    """Framework-neutral structured facts bundle."""

    facts: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class NarrativeExtract:
    """Framework-neutral narrative extract."""

    narrative_id: str
    text: str
    section_name: str | None = None


@dataclass(slots=True)
class PassageRecord:
    """Chunked narrative passage for retrieval."""

    chunk_id: str
    document_id: str
    section_name: str | None = None
    chunk_index: int = 0
    text: str = ""
    char_count: int | None = None
    source_confidence: str | None = None


@dataclass(slots=True)
class CompanyContextBundle:
    """Composite bundle intended for downstream consumers."""

    company: CompanyRecord
    identifiers: list[IdentifierRecord] = field(default_factory=list)
    listing: ListingRecord | None = None
    documents: list[DocumentRecord] = field(default_factory=list)
    facts: FactSet = field(default_factory=FactSet)
    narratives: list[NarrativeExtract] = field(default_factory=list)
