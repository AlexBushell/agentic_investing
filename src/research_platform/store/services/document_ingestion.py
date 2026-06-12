"""Services for persisting ingested documents and artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from research_platform.sources.nsm import NSMDownloadResult
from research_platform.store.models import Company, Document, DocumentArtifact
from research_platform.store.repositories.document_ingestion import DocumentIngestionRepository


@dataclass(slots=True)
class PersistedDocumentArtifact:
    """Summary of one persisted artifact."""

    artifact_id: str
    artifact_kind: str
    file_path: str


@dataclass(slots=True)
class DocumentPersistenceResult:
    """Summary of a persisted NSM document and its artifacts."""

    company_id: str
    document_id: str
    created_company: bool
    created_document: bool
    artifacts: list[PersistedDocumentArtifact] = field(default_factory=list)


class DocumentIngestionService:
    """Persist document registry records and filesystem artifacts."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = DocumentIngestionRepository(session)

    def persist_nsm_download_result(
        self,
        *,
        query: str,
        result: NSMDownloadResult,
        company_id: str | None = None,
    ) -> DocumentPersistenceResult:
        company, created_company = self._resolve_company(query=query, company_id=company_id)

        publication_date = _parse_nsm_date(
            result.selected_candidate.date_text if result.selected_candidate else None
        )
        title = result.selected_candidate.title if result.selected_candidate else None
        source_reference = result.selected_candidate.category if result.selected_candidate else None
        source_url = result.selected_candidate.href if result.selected_candidate else result.result_page_url

        document_role = _map_document_role(result.document_type)
        document = self.repo.get_document(
            company_id=company.company_id,
            source="NSM",
            document_role=document_role,
            title=title,
            publication_date=publication_date,
            source_reference=source_reference,
        )

        created_document = document is None
        if document is None:
            document = self.repo.add_document(
                Document(
                    company_id=company.company_id,
                    source="NSM",
                    document_role=document_role,
                    title=title,
                    publication_date=publication_date,
                    period_end=None,
                    source_url=source_url,
                    source_reference=source_reference,
                )
            )
        else:
            document.source_url = source_url
            document.source_reference = source_reference

        artifacts: list[PersistedDocumentArtifact] = []
        artifact_specs = [
            ("DOWNLOAD", result.downloaded_file),
            ("PRIMARY_REPORT", result.primary_report_file),
            ("SCREENSHOT", result.screenshot_path),
            ("HTML_SNAPSHOT", result.html_snapshot_path),
        ]
        for extracted_file in result.extracted_files:
            artifact_specs.append(("EXTRACTED_FILE", extracted_file))

        seen_paths: set[tuple[str, str]] = set()
        for artifact_kind, raw_path in artifact_specs:
            if not raw_path:
                continue
            key = (artifact_kind, raw_path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            artifact = self._ensure_artifact(
                document_id=document.document_id,
                artifact_kind=artifact_kind,
                raw_path=raw_path,
            )
            artifacts.append(
                PersistedDocumentArtifact(
                    artifact_id=str(artifact.artifact_id),
                    artifact_kind=artifact.artifact_kind,
                    file_path=artifact.file_path,
                )
            )

        return DocumentPersistenceResult(
            company_id=str(company.company_id),
            document_id=str(document.document_id),
            created_company=created_company,
            created_document=created_document,
            artifacts=artifacts,
        )

    def _resolve_company(self, *, query: str, company_id: str | None) -> tuple[Company, bool]:
        if company_id is not None:
            company = self.repo.get_company_by_id(company_id)
            if company is None:
                raise ValueError(f"Company not found: {company_id}")
            return company, False

        company = self.repo.get_company_by_name(query)
        if company is not None:
            return company, False

        company = self.repo.add_company(
            Company(
                name=query,
                legal_name=query,
                country=None,
            )
        )
        return company, True

    def _ensure_artifact(self, *, document_id, artifact_kind: str, raw_path: str) -> DocumentArtifact:
        artifact = self.repo.get_artifact(
            document_id=document_id,
            artifact_kind=artifact_kind,
            file_path=raw_path,
        )
        if artifact is None:
            path = Path(raw_path)
            artifact = self.repo.add_artifact(
                DocumentArtifact(
                    document_id=document_id,
                    artifact_kind=artifact_kind,
                    file_path=raw_path,
                    file_hash=_sha256_if_file(path),
                    mime_type=None,
                    format=path.suffix.lower().lstrip(".") or None,
                    size_bytes=path.stat().st_size if path.exists() and path.is_file() else None,
                )
            )
        else:
            path = Path(raw_path)
            artifact.file_hash = _sha256_if_file(path)
            artifact.format = path.suffix.lower().lstrip(".") or None
            artifact.size_bytes = path.stat().st_size if path.exists() and path.is_file() else None
        return artifact


def _map_document_role(document_type: str) -> str:
    mapping = {
        "annual-report": "ANNUAL_REPORT",
        "interim-report": "INTERIM_REPORT",
    }
    return mapping.get(document_type.strip().lower(), document_type.strip().upper().replace("-", "_"))


def _parse_nsm_date(value: str | None):
    if not value:
        return None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def _sha256_if_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
