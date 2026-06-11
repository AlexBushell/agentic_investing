"""Lightweight data transfer objects for company context access."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CompanyRecord:
    """Minimal company identity record returned by the access layer."""

    company_id: str
    name: str


@dataclass(slots=True)
class ListingRecord:
    """Minimal listing record returned by the access layer."""

    listing_id: str
    ticker: str
    exchange_code: str | None = None


@dataclass(slots=True)
class DocumentRecord:
    """Minimal document registry record returned by the access layer."""

    document_id: str
    document_role: str
    title: str | None = None


@dataclass(slots=True)
class ArtifactRecord:
    """Minimal file artifact record returned by the access layer."""

    artifact_id: str
    file_path: str
    artifact_kind: str


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
class MarketSnapshot:
    """Framework-neutral market snapshot."""

    as_of_date: str
    price: float | None = None
    market_cap: float | None = None


@dataclass(slots=True)
class CompanyContextBundle:
    """Composite bundle intended for downstream consumers."""

    company: CompanyRecord
    listing: ListingRecord | None = None
    documents: list[DocumentRecord] = field(default_factory=list)
    facts: FactSet = field(default_factory=FactSet)
    narratives: list[NarrativeExtract] = field(default_factory=list)
    market_snapshot: MarketSnapshot | None = None

