from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from research_platform.access.queries import SQLCompanyContextStore
from research_platform.store.models import (
    Base,
    Company,
    Document,
    DocumentArtifact,
    DocumentExtraction,
    Fact,
    Identifier,
    Listing,
    NarrativeExtract,
)


def _make_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed_store(session: Session) -> str:
    company = Company(name="Tesco PLC", legal_name="TESCO PLC", country="GB")
    session.add(company)
    session.flush()

    isin = Identifier(
        company_id=company.company_id,
        id_type="ISIN",
        id_value="GB0008847096",
        source="OPENFIGI",
        is_primary=True,
    )
    ticker = Identifier(
        company_id=company.company_id,
        id_type="YAHOO_TICKER",
        id_value="TSCO.L",
        source="DERIVED",
        is_primary=False,
    )
    session.add_all([isin, ticker])

    listing = Listing(
        company_id=company.company_id,
        ticker="TSCO",
        exchange_code="LN",
        security_type="Common Stock",
        market_sector="Equity",
        is_primary=True,
    )
    session.add(listing)
    session.flush()

    document = Document(
        company_id=company.company_id,
        source="NSM",
        document_role="ANNUAL_REPORT",
        title="Annual Report and Accounts 2025",
        publication_date=date(2026, 6, 11),
        source_url="https://example.test/report",
        source_reference="Annual Financial Report",
    )
    session.add(document)
    session.flush()

    artifact = DocumentArtifact(
        document_id=document.document_id,
        artifact_kind="PRIMARY_REPORT",
        file_path="C:/data/annual.xhtml",
        file_hash="hash",
        format="xhtml",
        size_bytes=123,
    )
    session.add(artifact)
    session.flush()

    extraction = DocumentExtraction(
        document_id=document.document_id,
        artifact_id=artifact.artifact_id,
        extraction_type="IXBRL_EXTRACTION",
        extractor_name="IXBRLExtractor",
        extractor_version="v1",
        payload_json={"ok": True},
    )
    session.add(extraction)
    session.flush()

    fact = Fact(
        company_id=company.company_id,
        document_id=document.document_id,
        extraction_id=extraction.extraction_id,
        concept="ifrs-full:Revenue",
        namespace="ifrs-full",
        period_end=date(2026, 2, 24),
        unit="GBP",
        value_numeric=Decimal("1000.0"),
        value_text="1000",
        dimensions_json=None,
        source_confidence="HIGH",
    )
    narrative = NarrativeExtract(
        company_id=company.company_id,
        document_id=document.document_id,
        extraction_id=extraction.extraction_id,
        section_name="ifrs-full:DisclosureOfGoingConcernExplanatory",
        text="Directors considered going concern assumptions.",
        char_count=44,
        source_confidence="HIGH",
    )
    session.add_all([fact, narrative])
    session.commit()

    return str(company.company_id)


def test_get_company_resolves_by_identifier():
    session = _make_session()
    company_id = _seed_store(session)
    store = SQLCompanyContextStore(session)

    company = store.get_company("GB0008847096")

    assert company.company_id == company_id
    assert company.name == "Tesco PLC"
    assert company.country == "GB"


def test_get_latest_documents_and_artifacts():
    session = _make_session()
    company_id = _seed_store(session)
    store = SQLCompanyContextStore(session)

    documents = store.get_latest_documents(company_id)
    artifacts = store.get_document_artifacts(documents[0].document_id)

    assert len(documents) == 1
    assert documents[0].document_role == "ANNUAL_REPORT"
    assert len(artifacts) == 1
    assert artifacts[0].artifact_kind == "PRIMARY_REPORT"

    company_artifacts = store.list_artifacts_for_company(company_id)
    artifact_detail = store.get_artifact(artifacts[0].artifact_id)

    assert len(company_artifacts) == 1
    assert company_artifacts[0].document.document_role == "ANNUAL_REPORT"
    assert artifact_detail.company.company_id == company_id
    assert artifact_detail.document.document_id == documents[0].document_id
    assert artifact_detail.artifact.artifact_id == artifacts[0].artifact_id


def test_get_fact_set_and_company_context():
    session = _make_session()
    company_id = _seed_store(session)
    store = SQLCompanyContextStore(session)

    fact_set = store.get_fact_set(company_id, document_role="ANNUAL_REPORT")
    bundle = store.build_company_context(company_id)

    assert len(fact_set.facts) == 1
    assert fact_set.facts[0]["concept"] == "ifrs-full:Revenue"
    assert bundle.company.company_id == company_id
    assert len(bundle.identifiers) == 2
    assert bundle.listing is not None
    assert len(bundle.documents) == 1
    assert len(bundle.facts.facts) == 1
    assert len(bundle.narratives) == 1
