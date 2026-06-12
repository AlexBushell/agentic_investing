"""Orchestration service for turning stored documents into retrieval-ready context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from research_platform.documents.ixbrl_extractor import IXBRLExtractionError, IXBRLExtractor
from research_platform.documents.text_extractor import TextExtractionError, extract_text
from research_platform.store.models import Document, DocumentArtifact
from research_platform.store.services.chunking import NarrativeChunkingService
from research_platform.store.services.extraction_persistence import (
    ExtractionPersistenceResult,
    ExtractionPersistenceService,
)


@dataclass(slots=True)
class DocumentMaterializationResult:
    document_id: str
    artifact_id: str
    artifact_path: str
    strategy: str
    extraction_id: str
    fact_count: int
    narrative_count: int
    chunk_count: int


class DocumentPipelineService:
    """Materialize stored document artifacts into extractions and chunks."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.extraction_service = ExtractionPersistenceService(session)
        self.chunking_service = NarrativeChunkingService(session)
        self.ixbrl_extractor = IXBRLExtractor()

    def materialize_document(
        self,
        *,
        document_id: str,
        strategy: str = "auto",
        chunk: bool = True,
        max_chars: int = 1200,
        overlap_chars: int = 150,
    ) -> DocumentMaterializationResult:
        document = self.session.get(Document, _coerce_uuid(document_id))
        if document is None:
            raise ValueError(f"Document not found: {document_id}")

        artifact = self._select_source_artifact(document.document_id)
        if artifact is None:
            raise ValueError(f"No suitable source artifact found for document {document_id}")

        selected_strategy = self._resolve_strategy(path=Path(artifact.file_path), strategy=strategy)
        extraction_result = self._persist_extraction(
            artifact=artifact,
            strategy=selected_strategy,
            document_id=str(document.document_id),
        )

        chunk_count = 0
        if chunk:
            chunk_result = self.chunking_service.chunk_document(
                document_id=str(document.document_id),
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
            chunk_count = chunk_result.chunk_count

        return DocumentMaterializationResult(
            document_id=str(document.document_id),
            artifact_id=str(artifact.artifact_id),
            artifact_path=artifact.file_path,
            strategy=selected_strategy,
            extraction_id=extraction_result.extraction_id,
            fact_count=extraction_result.fact_count,
            narrative_count=extraction_result.narrative_count,
            chunk_count=chunk_count,
        )

    def _select_source_artifact(self, document_id) -> DocumentArtifact | None:
        stmt = select(DocumentArtifact).where(DocumentArtifact.document_id == document_id)
        artifacts = self.session.execute(stmt).scalars().all()
        if not artifacts:
            return None

        def score(item: DocumentArtifact) -> tuple[int, int, int, str]:
            kind_score = {
                "PRIMARY_REPORT": 5,
                "EXTRACTED_FILE": 4,
                "DOWNLOAD": 3,
                "SOURCE_FILE": 2,
                "HTML_SNAPSHOT": 1,
                "SCREENSHOT": 0,
            }.get(item.artifact_kind, 0)
            format_score = {
                "xhtml": 5,
                "html": 4,
                "htm": 4,
                "pdf": 3,
                "xml": 2,
                "txt": 1,
            }.get((item.format or "").lower(), 0)
            ixbrl_bonus = 2 if _looks_like_ixbrl(Path(item.file_path)) else 0
            return (kind_score, format_score, ixbrl_bonus, item.file_path)

        ranked = sorted(artifacts, key=score, reverse=True)
        best = ranked[0]
        if score(best)[0] <= 0:
            return None
        return best

    def _resolve_strategy(self, *, path: Path, strategy: str) -> str:
        selected = strategy.strip().lower()
        if selected not in {"auto", "ixbrl", "text"}:
            raise ValueError("Strategy must be one of: auto, ixbrl, text")
        if selected != "auto":
            return selected
        return "ixbrl" if _looks_like_ixbrl(path) else "text"

    def _persist_extraction(
        self,
        *,
        artifact: DocumentArtifact,
        strategy: str,
        document_id: str,
    ) -> ExtractionPersistenceResult:
        path = Path(artifact.file_path)
        if strategy == "ixbrl":
            try:
                extraction = self.ixbrl_extractor.extract(path)
            except IXBRLExtractionError as exc:
                raise ValueError(f"iXBRL extraction failed for {path.name}: {exc}") from exc
            return self.extraction_service.persist_ixbrl_extraction(
                extraction=extraction,
                document_id=document_id,
            )

        try:
            text = extract_text(path)
        except TextExtractionError as exc:
            raise ValueError(f"Text extraction failed for {path.name}: {exc}") from exc
        return self.extraction_service.persist_text_extraction(
            file_path=path,
            text=text,
            document_id=document_id,
        )


def _looks_like_ixbrl(path: Path) -> bool:
    if path.suffix.lower() not in {".xhtml", ".html", ".htm", ".xml"}:
        return False
    if not path.exists() or not path.is_file():
        return False
    try:
        snippet = path.read_text(encoding="utf-8", errors="ignore")[:20000].lower()
    except OSError:
        return False
    return (
        "http://www.xbrl.org/2013/inlinexbrl" in snippet
        or "xmlns:ix=" in snippet
        or "ix:nonfraction" in snippet
        or "ix:nonnumeric" in snippet
    )


def _coerce_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return value
