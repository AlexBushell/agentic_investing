from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class DocumentRole:
    """Defines one logical document type and the NSM categories that can fulfil it."""
    role: str
    nsm_categories: tuple[str, ...]
    max_age_months: int


# Priority order within each role matters: earlier categories are preferred over later ones
# when two candidates are from the same date (e.g. iXBRL package beats RNS announcement).
NSM_MANIFEST: tuple[DocumentRole, ...] = (
    DocumentRole(
        role="annual",
        nsm_categories=(
            "Annual Financial Report",   # ESEF iXBRL package — preferred
            "Final Results",             # RNS-style audited results — common fallback
            "Preliminary Results",
            "Full Year Results",
        ),
        max_age_months=18,
    ),
    DocumentRole(
        role="halfyear",
        nsm_categories=(
            "Half-year Financial Report",  # iXBRL or RNS
            "Interim Report",
            "Half Yearly Report",
        ),
        max_age_months=12,
    ),
    DocumentRole(
        role="trading_update",
        nsm_categories=(
            "Trading Statement",
            "Management Statement",
            "Quarterly Financial Report",
        ),
        max_age_months=6,
    ),
)


class AcquiredDocument(BaseModel):
    role: str
    title: str
    date_text: Optional[str] = None
    category: Optional[str] = None
    downloaded_file: Optional[str] = None
    primary_report_file: Optional[str] = None
    notes: list[str] = Field(default_factory=list)


class AcquiredDocumentSet(BaseModel):
    query: str
    all_candidates: list[dict] = Field(default_factory=list)
    documents: list[AcquiredDocument] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def get(self, role: str) -> Optional[AcquiredDocument]:
        for doc in self.documents:
            if doc.role == role:
                return doc
        return None

    def get_post_period(self) -> Optional[AcquiredDocument]:
        """Return the best post-annual update: halfyear preferred, trading update as fallback."""
        return self.get("halfyear") or self.get("trading_update")
