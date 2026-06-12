"""Access-layer contract for downstream company context consumers."""

from __future__ import annotations

from typing import Protocol

from research_platform.access.dto import (
    ArtifactRecord,
    ArtifactWithProvenance,
    CompanyContextBundle,
    CompanyRecord,
    DocumentRecord,
    FactSet,
    IdentifierRecord,
    ListingRecord,
    MarketSnapshot,
    NarrativeExtract,
    PassageRecord,
)


class CompanyContextStore(Protocol):
    """Framework-neutral interface for retrieving company context."""

    def get_company(self, company_ref: str) -> CompanyRecord: ...

    def get_identifiers(self, company_id: str) -> list[IdentifierRecord]: ...

    def get_primary_listing(self, company_id: str) -> ListingRecord | None: ...

    def get_latest_documents(self, company_id: str) -> list[DocumentRecord]: ...

    def get_document_artifacts(self, document_id: str) -> list[ArtifactRecord]: ...

    def list_artifacts_for_company(self, company_id: str) -> list[ArtifactWithProvenance]: ...

    def get_artifact(self, artifact_id: str) -> ArtifactWithProvenance: ...

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

    def search_passages(
        self,
        company_id: str,
        *,
        query: str,
        document_role: str | None = None,
        limit: int = 20,
    ) -> list[PassageRecord]: ...

    def get_market_snapshot(self, company_id: str) -> MarketSnapshot | None: ...

    def build_company_context(self, company_ref: str) -> CompanyContextBundle: ...
