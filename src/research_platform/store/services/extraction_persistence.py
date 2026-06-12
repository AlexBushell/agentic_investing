"""Services for persisting document extraction outputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
import hashlib

from sqlalchemy.orm import Session

from research_platform.documents.ixbrl_extractor import IXBRLExtractionResult
from research_platform.store.models import DocumentArtifact, DocumentExtraction, Fact, NarrativeExtract
from research_platform.store.repositories.extraction_persistence import ExtractionPersistenceRepository


@dataclass(slots=True)
class ExtractionPersistenceResult:
    """Summary of one persisted extraction output."""

    document_id: str
    extraction_id: str
    fact_count: int
    narrative_count: int
    extraction_type: str


class ExtractionPersistenceService:
    """Persist extraction outputs to document_extractions and derived tables."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = ExtractionPersistenceRepository(session)

    def persist_ixbrl_extraction(
        self,
        *,
        extraction: IXBRLExtractionResult,
        document_id: str | None = None,
    ) -> ExtractionPersistenceResult:
        document, artifact = self._resolve_document_target(
            file_path=extraction.file_path,
            document_id=document_id,
        )

        stored_extraction = self._ensure_extraction(
            document_id=document.document_id,
            artifact_id=artifact.artifact_id if artifact else None,
            extraction_type="IXBRL_EXTRACTION",
            extractor_name="IXBRLExtractor",
            extractor_version="v1",
            payload_json=extraction.model_dump(mode="json"),
        )

        facts: list[Fact] = []
        narratives: list[NarrativeExtract] = []
        for item in extraction.facts:
            context = item.context
            period = context.period if context else {}
            dimensions = context.dimensions if context else {}
            fact_common = {
                "company_id": document.company_id,
                "document_id": document.document_id,
                "extraction_id": stored_extraction.extraction_id,
                "source_confidence": "HIGH",
            }

            if item.fact_type == "numeric" and item.concept:
                facts.append(
                    Fact(
                        concept=item.concept,
                        namespace=_namespace_of(item.concept),
                        period_start=_parse_iso_date(period.get("startDate")),
                        period_end=_parse_iso_date(period.get("endDate")),
                        instant_date=_parse_iso_date(period.get("instant")),
                        unit=item.unit,
                        value_numeric=_decimal_or_none(item.value),
                        value_text=item.raw_text,
                        dimensions_json=dimensions or None,
                        **fact_common,
                    )
                )
            elif item.fact_type == "narrative" and item.concept and item.text:
                narratives.append(
                    NarrativeExtract(
                        section_name=item.concept,
                        text=item.text,
                        char_count=len(item.text),
                        **fact_common,
                    )
                )

        self.repo.replace_facts(extraction_id=stored_extraction.extraction_id, facts=facts)
        self.repo.replace_narratives(
            extraction_id=stored_extraction.extraction_id,
            narratives=narratives,
        )

        return ExtractionPersistenceResult(
            document_id=str(document.document_id),
            extraction_id=str(stored_extraction.extraction_id),
            fact_count=len(facts),
            narrative_count=len(narratives),
            extraction_type="IXBRL_EXTRACTION",
        )

    def persist_text_extraction(
        self,
        *,
        file_path: Path,
        text: str,
        document_id: str | None = None,
    ) -> ExtractionPersistenceResult:
        document, artifact = self._resolve_document_target(
            file_path=str(file_path),
            document_id=document_id,
        )

        stored_extraction = self._ensure_extraction(
            document_id=document.document_id,
            artifact_id=artifact.artifact_id if artifact else None,
            extraction_type="TEXT_EXTRACTION",
            extractor_name="extract_text",
            extractor_version="v1",
            payload_json={
                "file_path": str(file_path),
                "char_count": len(text),
            },
        )

        narratives = [
            NarrativeExtract(
                company_id=document.company_id,
                document_id=document.document_id,
                extraction_id=stored_extraction.extraction_id,
                section_name=file_path.stem,
                text=text,
                char_count=len(text),
                source_confidence="HIGH",
            )
        ]
        self.repo.replace_narratives(
            extraction_id=stored_extraction.extraction_id,
            narratives=narratives,
        )

        return ExtractionPersistenceResult(
            document_id=str(document.document_id),
            extraction_id=str(stored_extraction.extraction_id),
            fact_count=0,
            narrative_count=1,
            extraction_type="TEXT_EXTRACTION",
        )

    def _resolve_document_target(self, *, file_path: str, document_id: str | None):
        artifact = self.repo.get_artifact_by_path(file_path)
        if artifact is not None:
            document = self.repo.get_document(artifact.document_id)
            if document is None:
                raise ValueError(f"Document not found for artifact path: {file_path}")
            if document_id is not None and str(document.document_id) != document_id:
                raise ValueError("Provided document_id does not match the registered artifact path.")
            return document, artifact

        if document_id is None:
            raise ValueError(
                f"File is not registered in the store: {file_path}. "
                "Persist the document first or pass --document-id."
            )

        document = self.repo.get_document(document_id)
        if document is None:
            raise ValueError(f"Document not found: {document_id}")
        artifact = self._ensure_source_artifact(
            document_id=document.document_id,
            file_path=file_path,
        )
        return document, artifact

    def _ensure_extraction(
        self,
        *,
        document_id,
        artifact_id,
        extraction_type: str,
        extractor_name: str,
        extractor_version: str,
        payload_json,
    ) -> DocumentExtraction:
        extraction = self.repo.get_extraction(
            document_id=document_id,
            artifact_id=artifact_id,
            extraction_type=extraction_type,
            extractor_name=extractor_name,
        )
        if extraction is None:
            extraction = self.repo.add_extraction(
                DocumentExtraction(
                    document_id=document_id,
                    artifact_id=artifact_id,
                    extraction_type=extraction_type,
                    extractor_name=extractor_name,
                    extractor_version=extractor_version,
                    payload_json=payload_json,
                )
            )
        else:
            extraction.extractor_version = extractor_version
            extraction.payload_json = payload_json
        return extraction

    def _ensure_source_artifact(self, *, document_id, file_path: str) -> DocumentArtifact:
        existing = self.repo.get_artifact_by_path(file_path)
        if existing is not None:
            if existing.document_id != document_id:
                raise ValueError("Source file path is already registered against a different document.")
            return existing

        path = Path(file_path)
        return self.repo.add_artifact(
            DocumentArtifact(
                document_id=document_id,
                artifact_kind=_infer_artifact_kind(path),
                file_path=str(path),
                file_hash=_sha256_if_file(path),
                mime_type=_infer_mime_type(path),
                format=path.suffix.lower().lstrip(".") or None,
                size_bytes=path.stat().st_size if path.exists() and path.is_file() else None,
            )
        )


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _decimal_or_none(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _namespace_of(concept: str | None) -> str | None:
    if not concept or ":" not in concept:
        return None
    return concept.split(":", 1)[0]


def _infer_artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".xhtml", ".html", ".htm", ".xml", ".xbrl"}:
        return "PRIMARY_REPORT"
    if suffix == ".pdf":
        return "DOWNLOAD"
    return "SOURCE_FILE"


def _infer_mime_type(path: Path) -> str | None:
    suffix = path.suffix.lower()
    mapping = {
        ".xhtml": "application/xhtml+xml",
        ".html": "text/html",
        ".htm": "text/html",
        ".xml": "application/xml",
        ".xbrl": "application/xml",
        ".pdf": "application/pdf",
        ".json": "application/json",
        ".txt": "text/plain",
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
