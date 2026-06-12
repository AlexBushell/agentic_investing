from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from research_platform.documents.ixbrl_extractor import IXBRLContext, IXBRLExtractionResult, IXBRLFact
from research_platform.store.models import (
    Base,
    Company,
    Document,
    DocumentArtifact,
    DocumentExtraction,
    Fact,
    NarrativeExtract,
)
from research_platform.store.services.extraction_persistence import ExtractionPersistenceService


def _make_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed_document(session: Session, tmp_path: Path) -> tuple[Document, DocumentArtifact]:
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

    file_path = tmp_path / "annual.xhtml"
    file_path.write_text("<html>xhtml</html>", encoding="utf-8")
    artifact = DocumentArtifact(
        document_id=document.document_id,
        artifact_kind="PRIMARY_REPORT",
        file_path=str(file_path),
        file_hash="hash",
        format="xhtml",
        size_bytes=file_path.stat().st_size,
    )
    session.add(artifact)
    session.flush()
    session.commit()

    return document, artifact


def _sample_ixbrl_result(file_path: str) -> IXBRLExtractionResult:
    context = IXBRLContext(
        id="c1",
        entity="Tesco PLC",
        period={"startDate": "2025-02-25", "endDate": "2026-02-24"},
        dimensions={},
    )
    instant_context = IXBRLContext(
        id="c2",
        entity="Tesco PLC",
        period={"instant": "2026-02-24"},
        dimensions={"ifrs-full:ClassOfShareAxis": "ifrs-full:OrdinarySharesMember"},
    )
    return IXBRLExtractionResult(
        file_path=file_path,
        context_count=2,
        numeric_fact_count=2,
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
                fact_type="numeric",
                concept="ifrs-full:IssuedCapital",
                context_ref="c2",
                context=instant_context,
                raw_text="250",
                value=250.0,
                unit="GBP",
            ),
            IXBRLFact(
                fact_type="narrative",
                concept="ifrs-full:DisclosureOfGoingConcernExplanatory",
                context_ref="c1",
                context=context,
                text="Directors considered going concern assumptions.",
            ),
        ],
        notes=[],
    )


def test_persist_ixbrl_extraction_creates_extraction_facts_and_narratives(tmp_path: Path):
    session = _make_session()
    document, artifact = _seed_document(session, tmp_path)

    persisted = ExtractionPersistenceService(session).persist_ixbrl_extraction(
        extraction=_sample_ixbrl_result(artifact.file_path),
    )
    session.commit()

    extraction_count = session.execute(select(func.count()).select_from(DocumentExtraction)).scalar_one()
    fact_count = session.execute(select(func.count()).select_from(Fact)).scalar_one()
    narrative_count = session.execute(select(func.count()).select_from(NarrativeExtract)).scalar_one()

    assert persisted.document_id == str(document.document_id)
    assert persisted.fact_count == 2
    assert persisted.narrative_count == 1
    assert extraction_count == 1
    assert fact_count == 2
    assert narrative_count == 1


def test_persist_text_extraction_creates_single_narrative(tmp_path: Path):
    session = _make_session()
    document, artifact = _seed_document(session, tmp_path)
    text = "Plain narrative extracted from document."

    persisted = ExtractionPersistenceService(session).persist_text_extraction(
        file_path=Path(artifact.file_path),
        text=text,
    )
    session.commit()

    extraction_count = session.execute(select(func.count()).select_from(DocumentExtraction)).scalar_one()
    narrative_count = session.execute(select(func.count()).select_from(NarrativeExtract)).scalar_one()

    assert persisted.document_id == str(document.document_id)
    assert persisted.fact_count == 0
    assert persisted.narrative_count == 1
    assert extraction_count == 1
    assert narrative_count == 1


def test_persist_ixbrl_extraction_is_idempotent_for_same_artifact(tmp_path: Path):
    session = _make_session()
    _, artifact = _seed_document(session, tmp_path)
    service = ExtractionPersistenceService(session)
    extraction = _sample_ixbrl_result(artifact.file_path)

    first = service.persist_ixbrl_extraction(extraction=extraction)
    session.commit()
    second = service.persist_ixbrl_extraction(extraction=extraction)
    session.commit()

    extraction_count = session.execute(select(func.count()).select_from(DocumentExtraction)).scalar_one()
    fact_count = session.execute(select(func.count()).select_from(Fact)).scalar_one()
    narrative_count = session.execute(select(func.count()).select_from(NarrativeExtract)).scalar_one()

    assert first.extraction_id == second.extraction_id
    assert extraction_count == 1
    assert fact_count == 2
    assert narrative_count == 1


def test_persist_text_extraction_registers_source_artifact_when_document_id_is_provided(tmp_path: Path):
    session = _make_session()
    document, _ = _seed_document(session, tmp_path)
    file_path = tmp_path / "unregistered_source.txt"
    file_path.write_text("New extracted narrative.", encoding="utf-8")

    persisted = ExtractionPersistenceService(session).persist_text_extraction(
        file_path=file_path,
        text="New extracted narrative.",
        document_id=str(document.document_id),
    )
    session.commit()

    artifacts = session.execute(select(DocumentArtifact)).scalars().all()
    matching = [item for item in artifacts if item.file_path == str(file_path)]

    assert persisted.document_id == str(document.document_id)
    assert len(matching) == 1
    assert matching[0].artifact_kind == "SOURCE_FILE"
    assert matching[0].file_hash is not None


def test_persist_ixbrl_extraction_matches_registered_artifact_with_normalized_path(tmp_path: Path):
    session = _make_session()
    _, artifact = _seed_document(session, tmp_path)

    normalized_path = artifact.file_path.replace("\\", "/")
    persisted = ExtractionPersistenceService(session).persist_ixbrl_extraction(
        extraction=_sample_ixbrl_result(normalized_path),
    )
    session.commit()

    extraction_count = session.execute(select(func.count()).select_from(DocumentExtraction)).scalar_one()

    assert persisted.fact_count == 2
    assert extraction_count == 1
