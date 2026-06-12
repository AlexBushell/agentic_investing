from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from research_platform.store.models import (
    Base,
    Company,
    Document,
    DocumentChunk,
    DocumentExtraction,
    NarrativeExtract,
)
from research_platform.store.services.chunking import NarrativeChunkingService


def _make_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed_document_with_narrative(session: Session) -> str:
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
    extraction = DocumentExtraction(
        document_id=document.document_id,
        artifact_id=None,
        extraction_type="TEXT_EXTRACTION",
        extractor_name="extract_text",
        extractor_version="v1",
        payload_json={},
    )
    session.add(extraction)
    session.flush()
    narrative = NarrativeExtract(
        company_id=company.company_id,
        document_id=document.document_id,
        extraction_id=extraction.extraction_id,
        section_name="principal_risks",
        text=(
            "The group faces supply chain disruption risk. "
            "Management mitigates this through inventory planning.\n\n"
            "A second paragraph covers financing and liquidity."
        ),
        char_count=140,
        source_confidence="HIGH",
    )
    session.add(narrative)
    session.commit()
    return str(document.document_id)


def test_chunk_document_creates_chunks():
    session = _make_session()
    document_id = _seed_document_with_narrative(session)

    result = NarrativeChunkingService(session).chunk_document(
        document_id=document_id,
        max_chars=80,
        overlap_chars=10,
    )
    session.commit()

    chunk_count = session.execute(select(func.count()).select_from(DocumentChunk)).scalar_one()
    assert result.document_id == document_id
    assert result.chunk_count >= 2
    assert chunk_count == result.chunk_count


def test_chunk_document_is_replaceable():
    session = _make_session()
    document_id = _seed_document_with_narrative(session)
    service = NarrativeChunkingService(session)

    first = service.chunk_document(document_id=document_id, max_chars=80, overlap_chars=10)
    session.commit()
    second = service.chunk_document(document_id=document_id, max_chars=120, overlap_chars=0)
    session.commit()

    chunk_count = session.execute(select(func.count()).select_from(DocumentChunk)).scalar_one()
    assert first.chunk_count >= second.chunk_count
    assert chunk_count == second.chunk_count
