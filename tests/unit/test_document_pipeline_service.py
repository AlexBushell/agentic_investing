from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from research_platform.documents.ixbrl_extractor import IXBRLContext, IXBRLExtractionResult, IXBRLFact
from research_platform.store.models import Base, Company, Document, DocumentArtifact, DocumentChunk, DocumentExtraction, Fact, NarrativeExtract
from research_platform.store.services.document_pipeline import DocumentPipelineService


def _make_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed_document(session: Session, *, file_path: Path, artifact_kind: str, file_format: str) -> str:
    company = Company(name="Tesco PLC", legal_name="Tesco PLC", country="GB")
    session.add(company)
    session.flush()
    document = Document(
        company_id=company.company_id,
        source="NSM",
        document_role="ANNUAL_REPORT",
        title="Annual Report and Accounts 2025",
    )
    session.add(document)
    session.flush()
    artifact = DocumentArtifact(
        document_id=document.document_id,
        artifact_kind=artifact_kind,
        file_path=str(file_path),
        file_hash="hash",
        mime_type=None,
        format=file_format,
        size_bytes=file_path.stat().st_size,
    )
    session.add(artifact)
    session.commit()
    return str(document.document_id)


def _sample_ixbrl_result(file_path: str) -> IXBRLExtractionResult:
    context = IXBRLContext(
        id="c1",
        entity="Tesco PLC",
        period={"startDate": "2025-02-25", "endDate": "2026-02-24"},
        dimensions={},
    )
    return IXBRLExtractionResult(
        file_path=file_path,
        context_count=1,
        numeric_fact_count=1,
        narrative_fact_count=1,
        facts=[
            IXBRLFact(
                fact_type="numeric",
                concept="ifrs-full:Revenue",
                context_ref="c1",
                context=context,
                raw_text="1000",
                value=1000.0,
                unit="GBP",
            ),
            IXBRLFact(
                fact_type="narrative",
                concept="ifrs-full:DisclosureOfGoingConcernExplanatory",
                context_ref="c1",
                context=context,
                text="Directors considered going concern assumptions. They reviewed liquidity.",
            ),
        ],
        notes=[],
    )


def test_materialize_document_uses_ixbrl_strategy_and_chunks(tmp_path: Path):
    session = _make_session()
    file_path = tmp_path / "annual.xhtml"
    file_path.write_text(
        "<html xmlns:ix='http://www.xbrl.org/2013/inlineXBRL'><body>ix:nonFraction</body></html>",
        encoding="utf-8",
    )
    document_id = _seed_document(
        session,
        file_path=file_path,
        artifact_kind="PRIMARY_REPORT",
        file_format="xhtml",
    )

    with patch(
        "research_platform.store.services.document_pipeline.IXBRLExtractor.extract",
        return_value=_sample_ixbrl_result(str(file_path)),
    ):
        result = DocumentPipelineService(session).materialize_document(document_id=document_id)
    session.commit()

    extraction_count = session.execute(select(func.count()).select_from(DocumentExtraction)).scalar_one()
    fact_count = session.execute(select(func.count()).select_from(Fact)).scalar_one()
    narrative_count = session.execute(select(func.count()).select_from(NarrativeExtract)).scalar_one()
    chunk_count = session.execute(select(func.count()).select_from(DocumentChunk)).scalar_one()

    assert result.strategy == "ixbrl"
    assert result.fact_count == 1
    assert result.narrative_count == 1
    assert result.chunk_count == chunk_count
    assert extraction_count == 1
    assert fact_count == 1
    assert narrative_count == 1
    assert chunk_count >= 1


def test_materialize_document_uses_text_strategy_for_pdf(tmp_path: Path):
    session = _make_session()
    file_path = tmp_path / "annual.pdf"
    file_path.write_bytes(b"%PDF-1.4 placeholder")
    document_id = _seed_document(
        session,
        file_path=file_path,
        artifact_kind="DOWNLOAD",
        file_format="pdf",
    )

    with patch(
        "research_platform.store.services.document_pipeline.extract_text",
        return_value="A plain narrative extraction.\n\nSecond paragraph for chunking.",
    ):
        result = DocumentPipelineService(session).materialize_document(document_id=document_id)
    session.commit()

    extraction_count = session.execute(select(func.count()).select_from(DocumentExtraction)).scalar_one()
    narrative_count = session.execute(select(func.count()).select_from(NarrativeExtract)).scalar_one()
    chunk_count = session.execute(select(func.count()).select_from(DocumentChunk)).scalar_one()

    assert result.strategy == "text"
    assert result.fact_count == 0
    assert result.narrative_count == 1
    assert extraction_count == 1
    assert narrative_count == 1
    assert chunk_count >= 1
