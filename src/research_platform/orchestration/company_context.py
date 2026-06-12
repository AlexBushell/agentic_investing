"""Orchestration service for deriving and building company context."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from research_platform.access.queries import SQLCompanyContextStore
from research_platform.core.config import Settings
from research_platform.core.logging import get_logger
from research_platform.sources.edgar import EdgarClient, EdgarError
from research_platform.sources.gleif import GLEIFClient
from research_platform.sources.nsm import NSMDownloadRequest, NSMDownloadService
from research_platform.store.services.company_identity import CompanyIdentityService
from research_platform.store.services.document_ingestion import DocumentIngestionService
from research_platform.store.services.document_pipeline import DocumentPipelineService
from research_platform.store.services.edgar_ingestion import EdgarIngestionService

logger = get_logger(__name__)


@dataclass(slots=True)
class ResolvedCompanyCandidate:
    market: str
    display_name: str
    summary: str
    payload: dict[str, object]


@dataclass(slots=True)
class CompanyContextDerivation:
    company: dict[str, object]
    filters: dict[str, object]
    selected_document_count: int
    derived_document_count: int
    derived_documents: list[dict[str, object]]
    skipped_documents: list[dict[str, object]]


@dataclass(slots=True)
class CompanyContextBuildResult:
    ingestion_summary: dict[str, object]
    derived_context: CompanyContextDerivation


def _select_documents_for_derivation(
    *,
    documents,
    document_roles: set[str],
    latest_only: bool,
    limit: int | None,
):
    selected = [
        document
        for document in documents
        if not document_roles or document.document_role in document_roles
    ]

    if latest_only:
        seen_roles: set[str] = set()
        latest = []
        for document in selected:
            if document.document_role in seen_roles:
                continue
            seen_roles.add(document.document_role)
            latest.append(document)
        selected = latest

    if limit is not None:
        selected = selected[:limit]

    return selected


def _derive_selected_documents(
    *,
    pipeline: DocumentPipelineService,
    selected_documents,
    strategy: str,
    chunk: bool,
    max_chars: int,
    overlap_chars: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    derived = []
    skipped = []
    for document_record in selected_documents:
        try:
            result = pipeline.materialize_document(
                document_id=document_record.document_id,
                strategy=strategy,
                chunk=chunk,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
        except Exception as exc:
            skipped.append(
                {
                    "document_id": document_record.document_id,
                    "document_role": document_record.document_role,
                    "title": document_record.title,
                    "reason": str(exc),
                }
            )
            continue

        derived.append(
            {
                "document_id": result.document_id,
                "document_role": document_record.document_role,
                "title": document_record.title,
                "artifact_id": result.artifact_id,
                "artifact_path": result.artifact_path,
                "strategy": result.strategy,
                "extraction_id": result.extraction_id,
                "fact_count": result.fact_count,
                "narrative_count": result.narrative_count,
                "chunk_count": result.chunk_count,
            }
        )

    return derived, skipped


class CompanyContextBuilderService:
    """Resolve, ingest, and derive company context for the CLI."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.store = SQLCompanyContextStore(session)
        self.pipeline = DocumentPipelineService(session)

    def derive(
        self,
        *,
        company_ref: str,
        document_roles: set[str],
        latest_only: bool,
        limit: int | None,
        strategy: str,
        chunk: bool,
        max_chars: int,
        overlap_chars: int,
    ) -> CompanyContextDerivation:
        company_record = self.store.get_company(company_ref)
        documents = self.store.get_latest_documents(company_record.company_id)
        selected_documents = _select_documents_for_derivation(
            documents=documents,
            document_roles=document_roles,
            latest_only=latest_only,
            limit=limit,
        )

        derived, skipped = _derive_selected_documents(
            pipeline=self.pipeline,
            selected_documents=selected_documents,
            strategy=strategy,
            chunk=chunk,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )

        return CompanyContextDerivation(
            company=asdict(company_record),
            filters={
                "document_roles": sorted(document_roles) if document_roles else [],
                "latest_only": latest_only,
                "limit": limit,
                "strategy": strategy,
                "chunk": chunk,
            },
            selected_document_count=len(selected_documents),
            derived_document_count=len(derived),
            derived_documents=derived,
            skipped_documents=skipped,
        )

    def build(
        self,
        *,
        chosen: ResolvedCompanyCandidate,
        uk_document_type: str,
        us_forms: str | None,
        us_limit: int,
        download: bool,
        derive_latest_only: bool,
        derive_limit: int | None,
        strategy: str,
        chunk: bool,
        max_chars: int,
        overlap_chars: int,
    ) -> CompanyContextBuildResult:
        if chosen.market == "uk":
            lei = str(chosen.payload["lei"])
            gleif_client = GLEIFClient()
            gleif_record = gleif_client.get_record(lei)
            gleif_record.isins = gleif_client.get_isins(lei)

            result = NSMDownloadService(settings=self.settings).run(
                NSMDownloadRequest(
                    query=gleif_record.legal_name,
                    document_type=uk_document_type,
                    headed=False,
                    browser_channel=None,
                    max_results=10,
                )
            )

            gleif_write = CompanyIdentityService(self.session).upsert_from_gleif(record=gleif_record)
            write_result = DocumentIngestionService(self.session).persist_nsm_download_result(
                query=gleif_record.legal_name,
                result=result,
                company_id=gleif_write.company_id,
            )
            target_company_ref = gleif_write.company_id
            ingestion_summary: dict[str, object] = {
                "market": "uk",
                "selected_company": {
                    "display_name": chosen.display_name,
                    "summary": chosen.summary,
                    "lei": lei,
                },
                "ingested_documents": [write_result.document_id],
                "document_type": uk_document_type,
            }
        else:
            cik = str(chosen.payload["cik"])
            filer_profile = chosen.payload.get("edgar_profile", {})
            suggested_forms = filer_profile.get("suggested_forms") or [
                "10-K",
                "10-Q",
                "8-K",
                "20-F",
                "6-K",
            ]
            forms_csv = us_forms or ",".join(suggested_forms)
            form_list = tuple(item.strip().upper() for item in forms_csv.split(",") if item.strip())

            edgar_client = EdgarClient(self.settings)
            submissions = edgar_client.discover_filings(cik=cik, forms=form_list, limit=us_limit)
            downloaded_files: dict[str, Path] = {}
            if download:
                for filing in submissions.filings:
                    try:
                        downloaded_files[filing.accession_number] = edgar_client.download_filing(filing)
                    except EdgarError as exc:
                        logger.warning(
                            "EDGAR download failed for %s: %s",
                            filing.accession_number,
                            exc,
                        )

            persisted = EdgarIngestionService(self.session).persist_submissions(
                submissions=submissions,
                downloaded_files=downloaded_files or None,
            )
            target_company_ref = persisted.company_id
            ingestion_summary = {
                "market": "us",
                "selected_company": {
                    "display_name": chosen.display_name,
                    "summary": chosen.summary,
                    "cik": cik,
                },
                "ingested_documents": persisted.document_ids,
                "forms": list(form_list),
                "filing_count": len(submissions.filings),
            }

        derived_context = self.derive(
            company_ref=target_company_ref,
            document_roles=set(),
            latest_only=derive_latest_only,
            limit=derive_limit,
            strategy=strategy,
            chunk=chunk,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )

        return CompanyContextBuildResult(
            ingestion_summary=ingestion_summary,
            derived_context=derived_context,
        )
