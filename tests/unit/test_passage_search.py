from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from research_platform.access.queries import SQLCompanyContextStore
from research_platform.store.models import Base, Company, Document, DocumentChunk


def _make_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_search_passages_returns_matching_chunks():
    session = _make_session()
    company = Company(name="Tesco PLC", legal_name="Tesco PLC", country="GB")
    session.add(company)
    session.flush()
    document = Document(
        company_id=company.company_id,
        source="NSM",
        document_role="TRADING_UPDATE",
        title="Trading Update",
    )
    session.add(document)
    session.flush()
    session.add_all(
        [
            DocumentChunk(
                company_id=company.company_id,
                document_id=document.document_id,
                extraction_id=None,
                narrative_id=None,
                section_name="trading",
                chunk_index=0,
                chunk_text="The trading update notes margin pressure in grocery.",
                char_count=53,
                source_confidence="HIGH",
            ),
            DocumentChunk(
                company_id=company.company_id,
                document_id=document.document_id,
                extraction_id=None,
                narrative_id=None,
                section_name="liquidity",
                chunk_index=1,
                chunk_text="Liquidity remains strong with ample headroom.",
                char_count=45,
                source_confidence="HIGH",
            ),
        ]
    )
    session.commit()

    passages = SQLCompanyContextStore(session).search_passages(
        str(company.company_id),
        query="margin",
        document_role="TRADING_UPDATE",
    )

    assert len(passages) == 1
    assert passages[0].section_name == "trading"
    assert "margin pressure" in passages[0].text
