"""Services for persisting EDGAR company and filing discovery results."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from research_platform.sources.edgar import EdgarCompanySubmissions, EdgarFiling, map_edgar_form_to_document_role
from research_platform.store.models import Company, Document, DocumentArtifact, Identifier, Listing
from research_platform.store.repositories.company_identity import CompanyIdentityRepository
from research_platform.store.repositories.document_ingestion import DocumentIngestionRepository


@dataclass(slots=True)
class EdgarIngestionResult:
    """Summary of one EDGAR ingestion run."""

    company_id: str
    created_company: bool
    document_ids: list[str] = field(default_factory=list)


class EdgarIngestionService:
    """Persist EDGAR discovery results into the company store."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.identity_repo = CompanyIdentityRepository(session)
        self.document_repo = DocumentIngestionRepository(session)

    def persist_submissions(
        self,
        *,
        submissions: EdgarCompanySubmissions,
        downloaded_files: dict[str, Path] | None = None,
    ) -> EdgarIngestionResult:
        company = self.identity_repo.get_company_by_identifier(id_type="CIK", id_value=submissions.cik)
        created_company = company is None
        if company is None:
            company = self.identity_repo.add_company(
                Company(
                    name=submissions.company_name,
                    legal_name=submissions.company_name,
                    country="US",
                )
            )
        else:
            company.name = submissions.company_name
            company.legal_name = submissions.company_name

        self._ensure_identifier(company_id=company.company_id, id_type="CIK", id_value=submissions.cik, source="EDGAR", is_primary=True)
        for ticker in submissions.tickers:
            self._ensure_identifier(company_id=company.company_id, id_type="TICKER", id_value=ticker, source="EDGAR")

        for index, ticker in enumerate(submissions.tickers):
            exchange_code = submissions.exchanges[index] if index < len(submissions.exchanges) else None
            listing = self.identity_repo.get_listing(
                company_id=company.company_id,
                ticker=ticker,
                exchange_code=exchange_code,
            )
            if listing is None:
                self.identity_repo.add_listing(
                    Listing(
                        company_id=company.company_id,
                        ticker=ticker,
                        exchange_code=exchange_code,
                        security_type=None,
                        market_sector="Equity",
                        currency=None,
                        is_primary=index == 0,
                    )
                )

        document_ids: list[str] = []
        for filing in submissions.filings:
            document = self._ensure_document(company_id=company.company_id, filing=filing)
            document_ids.append(str(document.document_id))
            if downloaded_files and filing.accession_number in downloaded_files:
                self._ensure_download_artifact(
                    document_id=document.document_id,
                    filing=filing,
                    path=downloaded_files[filing.accession_number],
                )

        return EdgarIngestionResult(
            company_id=str(company.company_id),
            created_company=created_company,
            document_ids=document_ids,
        )

    def _ensure_document(self, *, company_id, filing: EdgarFiling) -> Document:
        document = self.document_repo.get_document(
            company_id=company_id,
            source="EDGAR",
            document_role=map_edgar_form_to_document_role(filing.form),
            title=filing.primary_doc_description or f"{filing.form} filing",
            publication_date=_parse_date(filing.filing_date),
            source_reference=filing.accession_number,
        )
        if document is None:
            document = self.document_repo.add_document(
                Document(
                    company_id=company_id,
                    source="EDGAR",
                    document_role=map_edgar_form_to_document_role(filing.form),
                    title=filing.primary_doc_description or f"{filing.form} filing",
                    publication_date=_parse_date(filing.filing_date),
                    period_end=_parse_date(filing.report_date),
                    source_url=filing.filing_href,
                    source_reference=filing.accession_number,
                )
            )
        else:
            document.source_url = filing.filing_href
            document.period_end = _parse_date(filing.report_date)
        return document

    def _ensure_download_artifact(self, *, document_id, filing: EdgarFiling, path: Path) -> DocumentArtifact:
        artifact = self.document_repo.get_artifact(
            document_id=document_id,
            artifact_kind="DOWNLOAD",
            file_path=str(path),
        )
        if artifact is None:
            artifact = self.document_repo.add_artifact(
                DocumentArtifact(
                    document_id=document_id,
                    artifact_kind="DOWNLOAD",
                    file_path=str(path),
                    file_hash=_sha256_if_file(path),
                    mime_type=_infer_mime_type(path),
                    format=path.suffix.lower().lstrip(".") or None,
                    size_bytes=path.stat().st_size if path.exists() else None,
                )
            )
        else:
            artifact.file_hash = _sha256_if_file(path)
            artifact.mime_type = _infer_mime_type(path)
            artifact.format = path.suffix.lower().lstrip(".") or None
            artifact.size_bytes = path.stat().st_size if path.exists() else None
        return artifact

    def _ensure_identifier(
        self,
        *,
        company_id,
        id_type: str,
        id_value: str,
        source: str,
        is_primary: bool = False,
    ) -> Identifier:
        identifier = self.identity_repo.get_identifier(
            company_id=company_id,
            id_type=id_type,
            id_value=id_value,
        )
        if identifier is None:
            identifier = self.identity_repo.add_identifier(
                Identifier(
                    company_id=company_id,
                    id_type=id_type,
                    id_value=id_value,
                    source=source,
                    is_primary=is_primary,
                )
            )
        else:
            identifier.source = source
            if is_primary:
                identifier.is_primary = True
        return identifier


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        from datetime import date

        return date.fromisoformat(value)
    except ValueError:
        return None


def _infer_mime_type(path: Path) -> str | None:
    suffix = path.suffix.lower()
    mapping = {
        ".htm": "text/html",
        ".html": "text/html",
        ".xhtml": "application/xhtml+xml",
        ".xml": "application/xml",
        ".txt": "text/plain",
        ".pdf": "application/pdf",
    }
    return mapping.get(suffix)


def _sha256_if_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
