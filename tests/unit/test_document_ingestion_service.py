from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from research_platform.sources.nsm import NSMCandidate, NSMDownloadResult
from research_platform.store.models import Base, Company, Document, DocumentArtifact
from research_platform.store.services.document_ingestion import DocumentIngestionService


def _make_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_nsm_result(tmp_path: Path) -> NSMDownloadResult:
    downloaded = tmp_path / "nsm_annual.html"
    downloaded.write_text("<html>annual report</html>", encoding="utf-8")
    screenshot = tmp_path / "nsm_page.png"
    screenshot.write_bytes(b"png")
    snapshot = tmp_path / "nsm_page.html"
    snapshot.write_text("<html>snapshot</html>", encoding="utf-8")
    extracted = tmp_path / "reports" / "annual.xhtml"
    extracted.parent.mkdir(parents=True, exist_ok=True)
    extracted.write_text("<html>xhtml</html>", encoding="utf-8")

    return NSMDownloadResult(
        query="Tesco PLC",
        document_type="annual-report",
        acquired_at=datetime.now(UTC),
        base_url="https://data.fca.org.uk/#/nsm/nationalstoragemechanism",
        result_page_url="https://example.test/results",
        candidates=[],
        selected_candidate=NSMCandidate(
            title="Annual Report and Accounts 2025",
            date_text="11/06/2026 10:30",
            organisation_name="Tesco PLC",
            category="Annual Financial Report",
            href="https://example.test/report",
        ),
        downloaded_file=str(downloaded),
        extracted_dir=str(extracted.parent),
        primary_report_file=str(extracted),
        extracted_files=[str(extracted)],
        sha256="abc123",
        screenshot_path=str(screenshot),
        html_snapshot_path=str(snapshot),
        notes=[],
    )


def test_persist_nsm_download_result_creates_company_document_and_artifacts(tmp_path: Path):
    session = _make_session()

    result = DocumentIngestionService(session).persist_nsm_download_result(
        query="Tesco PLC",
        result=_make_nsm_result(tmp_path),
    )
    session.commit()

    companies = session.execute(select(Company)).scalars().all()
    documents = session.execute(select(Document)).scalars().all()
    artifacts = session.execute(select(DocumentArtifact)).scalars().all()

    assert result.created_company is True
    assert result.created_document is True
    assert len(companies) == 1
    assert companies[0].name == "Tesco PLC"
    assert len(documents) == 1
    assert documents[0].document_role == "ANNUAL_REPORT"
    assert documents[0].title == "Annual Report and Accounts 2025"
    assert len(artifacts) == 5
    assert {artifact.artifact_kind for artifact in artifacts} == {
        "DOWNLOAD",
        "PRIMARY_REPORT",
        "SCREENSHOT",
        "HTML_SNAPSHOT",
        "EXTRACTED_FILE",
    }


def test_persist_nsm_download_result_is_idempotent_for_same_document(tmp_path: Path):
    session = _make_session()
    service = DocumentIngestionService(session)
    nsm_result = _make_nsm_result(tmp_path)

    first = service.persist_nsm_download_result(query="Tesco PLC", result=nsm_result)
    session.commit()
    second = service.persist_nsm_download_result(query="Tesco PLC", result=nsm_result)
    session.commit()

    company_count = session.execute(select(func.count()).select_from(Company)).scalar_one()
    document_count = session.execute(select(func.count()).select_from(Document)).scalar_one()
    artifact_count = session.execute(select(func.count()).select_from(DocumentArtifact)).scalar_one()

    assert first.company_id == second.company_id
    assert first.document_id == second.document_id
    assert second.created_company is False
    assert second.created_document is False
    assert company_count == 1
    assert document_count == 1
    assert artifact_count == 5
