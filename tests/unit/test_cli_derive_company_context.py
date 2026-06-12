from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import patch

from typer.testing import CliRunner

from research_platform.access.dto import CompanyRecord
from research_platform.cli import (
    app,
    _choose_company_candidate,
    _normalize_document_roles,
    _rank_uk_candidates,
)
from research_platform.core.config import Settings
from research_platform.sources.gleif import GLEIFRecord
from research_platform.store.services.company_context_builder import (
    ResolvedCompanyCandidate,
    _select_documents_for_derivation,
)


runner = CliRunner()


@dataclass
class _DocumentRecord:
    document_id: str
    document_role: str
    title: str | None = None


def _parse_trailing_json(stdout: str) -> dict:
    decoder = json.JSONDecoder()
    text = stdout.strip()
    last_payload = None
    index = 0
    while index < len(text):
        brace = text.find("{", index)
        if brace == -1:
            break
        try:
            payload, next_index = decoder.raw_decode(text, brace)
        except json.JSONDecodeError:
            index = brace + 1
            continue
        last_payload = payload
        index = next_index
    if last_payload is None:
        raise AssertionError("No JSON payload found in stdout")
    return last_payload


def test_normalize_document_roles():
    assert _normalize_document_roles(["annual-report", " INTERIM_REPORT "]) == {
        "ANNUAL_REPORT",
        "INTERIM_REPORT",
    }


def test_select_documents_for_derivation_latest_only_keeps_first_per_role():
    documents = [
        _DocumentRecord("1", "ANNUAL_REPORT", "Annual 2026"),
        _DocumentRecord("2", "ANNUAL_REPORT", "Annual 2025"),
        _DocumentRecord("3", "INTERIM_REPORT", "Interim 2026"),
    ]

    selected = _select_documents_for_derivation(
        documents=documents,
        document_roles={"ANNUAL_REPORT", "INTERIM_REPORT"},
        latest_only=True,
        limit=None,
    )

    assert [item.document_id for item in selected] == ["1", "3"]


def test_choose_company_candidate_uses_prompt_for_multiple_candidates():
    candidates = [
        ResolvedCompanyCandidate("uk", "The Gym Group PLC", "UK | LEI 123", {"lei": "123"}),
        ResolvedCompanyCandidate("us", "InMode Ltd.", "US | CIK 456", {"cik": "456"}),
    ]

    with patch("research_platform.cli.typer.prompt", return_value=2):
        selected = _choose_company_candidate(candidates=candidates)

    assert selected.market == "us"
    assert selected.display_name == "InMode Ltd."


def test_rank_uk_candidates_prefers_isin_bearing_plc_over_related_entities():
    results = [
        GLEIFRecord(lei="1", legal_name="IG GROUP LIMITED", country="GB", isins=[]),
        GLEIFRecord(lei="2", legal_name="IG GROUP HOLDINGS PLC", country="GB", isins=["GB00B06QFB75"]),
        GLEIFRecord(
            lei="3",
            legal_name="IG GROUP HOLDINGS PLC HMRC APPROVED SHARE INCENTIVE PLAN",
            country="GB",
            isins=[],
        ),
    ]

    ranked = _rank_uk_candidates(query="IG Group", results=results)

    assert ranked[0].lei == "2"


def test_derive_company_context_returns_derived_and_skipped_documents():
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        sec_user_agent="company-intelligence-store test@example.com",
    )
    documents = [
        _DocumentRecord("doc-1", "ANNUAL_REPORT", "Annual 2026"),
        _DocumentRecord("doc-2", "INTERIM_REPORT", "Interim 2026"),
    ]

    class _Store:
        def get_company(self, company):
            return CompanyRecord(
                company_id="company-1",
                name="Tesco PLC",
                legal_name="Tesco PLC",
                country="GB",
            )

        def get_latest_documents(self, company_id):
            return documents

    class _Pipeline:
        def __init__(self, session):
            self.session = session

        def materialize_document(self, *, document_id, strategy, chunk, max_chars, overlap_chars):
            if document_id == "doc-2":
                raise ValueError("No suitable source artifact found")
            return type(
                "Result",
                (),
                {
                    "document_id": document_id,
                    "artifact_id": "artifact-1",
                    "artifact_path": "data/downloads/nsm/tesco/annual.xhtml",
                    "strategy": "ixbrl",
                    "extraction_id": "extract-1",
                    "fact_count": 42,
                    "narrative_count": 8,
                    "chunk_count": 12,
                },
            )()

    @contextmanager
    def _fake_session_scope(_settings):
        yield object()

    with (
        patch("research_platform.cli.get_settings", return_value=settings),
        patch("research_platform.cli.session_scope", _fake_session_scope),
        patch("research_platform.store.services.company_context_builder.SQLCompanyContextStore", return_value=_Store()),
        patch("research_platform.store.services.company_context_builder.DocumentPipelineService", _Pipeline),
    ):
        result = runner.invoke(
            app,
            [
                "derive-company-context",
                "--company",
                "Tesco PLC",
                "--document-role",
                "ANNUAL_REPORT",
                "--document-role",
                "INTERIM_REPORT",
                "--all-matching",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["company"]["company_id"] == "company-1"
    assert payload["selected_document_count"] == 2
    assert payload["derived_document_count"] == 1
    assert payload["derived_documents"][0]["document_id"] == "doc-1"
    assert payload["skipped_documents"][0]["document_id"] == "doc-2"


def test_build_company_context_uses_prompted_selection_and_runs_us_flow():
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        sec_user_agent="company-intelligence-store test@example.com",
    )
    candidates = [
        ResolvedCompanyCandidate("uk", "The Gym Group PLC", "UK | LEI 123", {"lei": "123"}),
        ResolvedCompanyCandidate(
            "us",
            "InMode Ltd.",
            "US | CIK 0001742692 | foreign_private_issuer | forms 20-F,6-K",
            {
                "cik": "0001742692",
                "ticker": "INMD",
                "title": "InMode Ltd.",
                "edgar_profile": {"suggested_forms": ["20-F", "6-K"]},
            },
        ),
    ]
    documents = [_DocumentRecord("doc-1", "ANNUAL_REPORT", "Annual 2026")]

    class _Store:
        def get_company(self, company):
            return CompanyRecord(
                company_id="company-1",
                name="InMode Ltd.",
                legal_name="InMode Ltd.",
                country="US",
            )

        def get_latest_documents(self, company_id):
            return documents

    class _Pipeline:
        def __init__(self, session):
            self.session = session

        def materialize_document(self, *, document_id, strategy, chunk, max_chars, overlap_chars):
            return type(
                "Result",
                (),
                {
                    "document_id": document_id,
                    "artifact_id": "artifact-1",
                    "artifact_path": "data/downloads/edgar/inmode/20-F_test.htm",
                    "strategy": "text",
                    "extraction_id": "extract-1",
                    "fact_count": 0,
                    "narrative_count": 1,
                    "chunk_count": 2,
                },
            )()

    class _EdgarClient:
        def __init__(self, settings):
            self.settings = settings

        def discover_filings(self, *, cik, forms, limit):
            return type(
                "Submissions",
                (),
                {
                    "filings": [
                        type(
                            "Filing",
                            (),
                            {"accession_number": "0001742692-26-000001"},
                        )()
                    ],
                },
            )()

        def download_filing(self, filing):
            from pathlib import Path

            return Path("data/downloads/edgar/inmode/20-F_test.htm")

    class _EdgarIngestionService:
        def __init__(self, session):
            self.session = session

        def persist_submissions(self, *, submissions, downloaded_files=None):
            return type(
                "Persisted",
                (),
                {
                    "company_id": "company-1",
                    "document_ids": ["doc-1"],
                },
            )()

    @contextmanager
    def _fake_session_scope(_settings):
        yield object()

    with (
        patch("research_platform.cli.get_settings", return_value=settings),
        patch("research_platform.cli._resolve_company_candidates", return_value=candidates),
        patch("research_platform.cli.typer.prompt", return_value=2),
        patch("research_platform.cli.session_scope", _fake_session_scope),
        patch("research_platform.store.services.company_context_builder.SQLCompanyContextStore", return_value=_Store()),
        patch("research_platform.store.services.company_context_builder.DocumentPipelineService", _Pipeline),
        patch("research_platform.store.services.company_context_builder.EdgarClient", _EdgarClient),
        patch("research_platform.store.services.company_context_builder.EdgarIngestionService", _EdgarIngestionService),
    ):
        result = runner.invoke(app, ["build-company-context", "Inmode"])

    assert result.exit_code == 0
    payload = _parse_trailing_json(result.stdout)
    assert payload["resolution"]["market"] == "us"
    assert payload["resolution"]["payload"]["cik"] == "0001742692"
    assert payload["ingestion"]["forms"] == ["20-F", "6-K"]
    assert payload["derived_company_context"]["derived_document_count"] == 1
