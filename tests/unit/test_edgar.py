from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from research_platform.core.config import Settings
from research_platform.sources.edgar import (
    EdgarClient,
    EdgarAnnualHistoryDiscoveryResult,
    EdgarCompanySubmissions,
    EdgarFilerProfile,
    EdgarFiling,
    EdgarError,
    _group_edgar_annual_filings_by_year,
    _missing_edgar_years,
    _normalise_cik,
    infer_edgar_filing_family,
    map_edgar_form_to_document_role,
)
from research_platform.store.models import Base, Company, Document, DocumentArtifact, Identifier, Listing
from research_platform.store.services.edgar_ingestion import EdgarIngestionService


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        sec_user_agent="company-intelligence-store admin@example.com",
        sec_download_dir=tmp_path / "downloads",
        sec_artifact_dir=tmp_path / "artifacts",
    )


def _make_response(payload: dict):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


def _make_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_normalise_cik():
    assert _normalise_cik("320193") == "0000320193"
    assert _normalise_cik("0000320193") == "0000320193"


def test_map_edgar_form_to_document_role():
    assert map_edgar_form_to_document_role("10-K") == "ANNUAL_REPORT"
    assert map_edgar_form_to_document_role("10-Q") == "INTERIM_REPORT"
    assert map_edgar_form_to_document_role("8-K") == "TRADING_UPDATE"


def test_infer_edgar_filing_family_detects_foreign_private_issuer_from_forms():
    family, forms = infer_edgar_filing_family(
        entity_type=None,
        recent_forms=["6-K", "20-F"],
    )
    assert family == "foreign_private_issuer"
    assert forms == ["20-F", "6-K"]


def test_infer_edgar_filing_family_detects_domestic_issuer_from_forms():
    family, forms = infer_edgar_filing_family(
        entity_type=None,
        recent_forms=["8-K", "10-Q", "10-K"],
    )
    assert family == "domestic_issuer"
    assert forms == ["10-K", "10-Q", "8-K"]


def test_discover_filings_reads_submissions_payload(tmp_path: Path):
    payload = {
        "name": "APPLE INC",
        "tickers": ["AAPL"],
        "exchanges": ["Nasdaq"],
        "filings": {
            "recent": {
                "form": ["10-K", "8-K"],
                "accessionNumber": ["0000320193-24-000123", "0000320193-24-000124"],
                "primaryDocument": ["a10-k2024.htm", "a8-k2024.htm"],
                "filingDate": ["2024-11-01", "2024-11-10"],
                "reportDate": ["2024-09-28", ""],
                "primaryDocDescription": ["Annual report", "Current report"],
                "isXBRL": [1, 0],
            }
        },
    }
    client = EdgarClient(_settings(tmp_path))

    with patch("research_platform.sources.edgar.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = _make_response(payload)
        mock_client_cls.return_value = mock_client
        result = client.discover_filings(cik="320193", forms=("10-K",), limit=10)

    assert result.company_name == "APPLE INC"
    assert result.tickers == ["AAPL"]
    assert len(result.filings) == 1
    assert result.filings[0].form == "10-K"
    assert result.filings[0].accession_number == "0000320193-24-000123"
    assert "320193" in result.filings[0].filing_href


def test_inspect_filer_builds_foreign_private_issuer_profile(tmp_path: Path):
    payload = {
        "name": "InMode Ltd.",
        "entityType": "other",
        "tickers": ["INMD"],
        "exchanges": ["Nasdaq"],
        "filings": {
            "recent": {
                "form": ["6-K", "20-F", "6-K"],
            }
        },
    }
    client = EdgarClient(_settings(tmp_path))

    with patch("research_platform.sources.edgar.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = _make_response(payload)
        mock_client_cls.return_value = mock_client
        result = client.inspect_filer(cik="1742692")

    assert isinstance(result, EdgarFilerProfile)
    assert result.cik == "0001742692"
    assert result.filing_family == "foreign_private_issuer"
    assert result.suggested_forms == ["20-F", "6-K"]
    assert result.recent_forms_sample[:2] == ["6-K", "20-F"]


def test_group_edgar_annual_filings_by_year_selects_one_filing_per_year():
    filings = [
        EdgarFiling(
            cik="0001742692",
            company_name="InMode Ltd.",
            form="20-F",
            filing_date="2026-02-20",
            accession_number="0001742692-26-000002",
            primary_document="inmd-2025.htm",
            report_date="2025-12-31",
            filing_href="https://example.test/2026-20f.htm",
        ),
        EdgarFiling(
            cik="0001742692",
            company_name="InMode Ltd.",
            form="20-F",
            filing_date="2025-02-21",
            accession_number="0001742692-25-000001",
            primary_document="inmd-2024.htm",
            report_date="2024-12-31",
            filing_href="https://example.test/2025-20f.htm",
        ),
    ]

    selected = _group_edgar_annual_filings_by_year(filings, years=5)

    assert [item.year for item in selected] == [2025, 2024]
    assert selected[0].selected_filing.accession_number == "0001742692-26-000002"


def test_missing_edgar_years_reports_gaps():
    selected = _group_edgar_annual_filings_by_year(
        [
            EdgarFiling(
                cik="0000320193",
                company_name="APPLE INC",
                form="10-K",
                filing_date="2026-11-01",
                accession_number="0000320193-26-000001",
                primary_document="a10-k2026.htm",
                report_date="2026-09-28",
                filing_href="https://example.test/2026-10k.htm",
            ),
            EdgarFiling(
                cik="0000320193",
                company_name="APPLE INC",
                form="10-K",
                filing_date="2024-11-01",
                accession_number="0000320193-24-000001",
                primary_document="a10-k2024.htm",
                report_date="2024-09-28",
                filing_href="https://example.test/2024-10k.htm",
            ),
        ],
        years=3,
    )

    missing = _missing_edgar_years(selected, years=3)

    assert missing == [2025]


def test_discover_annual_history_builds_result(tmp_path: Path):
    payload = {
        "name": "InMode Ltd.",
        "entityType": "other",
        "tickers": ["INMD"],
        "exchanges": ["Nasdaq"],
        "filings": {
            "recent": {
                "form": ["6-K", "20-F", "20-F"],
                "accessionNumber": ["0001742692-26-000010", "0001742692-26-000002", "0001742692-25-000001"],
                "primaryDocument": ["inmd-6k.htm", "inmd-2025.htm", "inmd-2024.htm"],
                "filingDate": ["2026-03-01", "2026-02-20", "2025-02-21"],
                "reportDate": ["", "2025-12-31", "2024-12-31"],
                "primaryDocDescription": ["Current report", "Annual report", "Annual report"],
                "isXBRL": [0, 1, 1],
            }
        },
    }
    client = EdgarClient(_settings(tmp_path))

    with patch("research_platform.sources.edgar.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = _make_response(payload)
        mock_client_cls.return_value = mock_client
        result = client.discover_annual_history(cik="1742692", years=5, limit=100)

    assert isinstance(result, EdgarAnnualHistoryDiscoveryResult)
    assert result.filing_family == "foreign_private_issuer"
    assert result.annual_forms == ["20-F"]
    assert [item.year for item in result.selected_years] == [2025, 2024]


def test_download_filing_writes_file(tmp_path: Path):
    client = EdgarClient(_settings(tmp_path))
    filing = EdgarFiling(
        cik="0000320193",
        company_name="APPLE INC",
        form="10-K",
        filing_date="2024-11-01",
        accession_number="0000320193-24-000123",
        primary_document="a10-k2024.htm",
        filing_href="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/a10-k2024.htm",
    )

    with patch("research_platform.sources.edgar.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        response = MagicMock()
        response.content = b"<html>edgar</html>"
        response.raise_for_status = MagicMock()
        mock_client.get.return_value = response
        mock_client_cls.return_value = mock_client
        path = client.download_filing(filing)

    assert path.exists()
    assert path.read_bytes() == b"<html>edgar</html>"


def test_persist_submissions_creates_company_and_documents():
    session = _make_session()
    submissions = EdgarCompanySubmissions(
        cik="0000320193",
        company_name="APPLE INC",
        tickers=["AAPL"],
        exchanges=["Nasdaq"],
        filings=[
            EdgarFiling(
                cik="0000320193",
                company_name="APPLE INC",
                form="10-K",
                filing_date="2024-11-01",
                accession_number="0000320193-24-000123",
                primary_document="a10-k2024.htm",
                report_date="2024-09-28",
                primary_doc_description="Annual report",
                filing_href="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/a10-k2024.htm",
            )
        ],
    )

    result = EdgarIngestionService(session).persist_submissions(submissions=submissions)
    session.commit()

    company_count = session.execute(select(func.count()).select_from(Company)).scalar_one()
    identifier_count = session.execute(select(func.count()).select_from(Identifier)).scalar_one()
    listing_count = session.execute(select(func.count()).select_from(Listing)).scalar_one()
    document_count = session.execute(select(func.count()).select_from(Document)).scalar_one()

    assert result.created_company is True
    assert company_count == 1
    assert identifier_count == 2
    assert listing_count == 1
    assert document_count == 1


def test_persist_submissions_stores_download_artifact_metadata(tmp_path: Path):
    session = _make_session()
    downloaded = tmp_path / "a10-k2024.htm"
    downloaded.write_text("<html>edgar</html>", encoding="utf-8")
    submissions = EdgarCompanySubmissions(
        cik="0000320193",
        company_name="APPLE INC",
        tickers=["AAPL"],
        exchanges=["Nasdaq"],
        filings=[
            EdgarFiling(
                cik="0000320193",
                company_name="APPLE INC",
                form="10-K",
                filing_date="2024-11-01",
                accession_number="0000320193-24-000123",
                primary_document="a10-k2024.htm",
                report_date="2024-09-28",
                primary_doc_description="Annual report",
                filing_href="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/a10-k2024.htm",
            )
        ],
    )

    EdgarIngestionService(session).persist_submissions(
        submissions=submissions,
        downloaded_files={"0000320193-24-000123": downloaded},
    )
    session.commit()

    artifact = session.execute(select(DocumentArtifact)).scalar_one()
    assert artifact.file_hash is not None
    assert artifact.mime_type == "text/html"
    assert artifact.size_bytes == downloaded.stat().st_size


def test_discover_filings_raises_for_bad_http(tmp_path: Path):
    import httpx

    client = EdgarClient(_settings(tmp_path))
    with patch("research_platform.sources.edgar.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.side_effect = httpx.ConnectError("boom")
        mock_client_cls.return_value = mock_client
        try:
            client.discover_filings(cik="320193")
        except EdgarError as exc:
            assert "request failed" in str(exc)
        else:
            raise AssertionError("Expected EdgarError")
