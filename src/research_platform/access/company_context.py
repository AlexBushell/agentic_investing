"""Access-layer contract for downstream company context consumers."""

from __future__ import annotations

from typing import Protocol

from research_platform.access.dto import (
    ArtifactRecord,
    CompanyContextBundle,
    CompanyRecord,
    DocumentRecord,
    FactSet,
    ListingRecord,
    MarketSnapshot,
    NarrativeExtract,
)


class CompanyContextStore(Protocol):
    """Framework-neutral interface for retrieving company context."""

    def get_company(self, company_ref: str) -> CompanyRecord: ...

    def get_primary_listing(self, company_id: str) -> ListingRecord | None: ...

    def get_latest_documents(self, company_id: str) -> list[DocumentRecord]: ...

    def get_document_artifacts(self, document_id: str) -> list[ArtifactRecord]: ...

    def get_fact_set(
        self,
        company_id: str,
        *,
        document_role: str | None = None,
    ) -> FactSet: ...

    def get_narrative_extracts(
        self,
        company_id: str,
        *,
        document_role: str | None = None,
    ) -> list[NarrativeExtract]: ...

    def get_market_snapshot(self, company_id: str) -> MarketSnapshot | None: ...

    def build_company_context(self, company_ref: str) -> CompanyContextBundle: ...

