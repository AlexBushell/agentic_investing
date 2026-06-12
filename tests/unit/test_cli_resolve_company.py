import json
from unittest.mock import patch

from typer.testing import CliRunner

from research_platform.cli import app
from research_platform.core.config import Settings
from research_platform.sources.edgar import EdgarCompanySubmissions, EdgarFilerProfile
from research_platform.sources.gleif import GLEIFRecord
from research_platform.sources.sec_tickers import SECTickerRecord


runner = CliRunner()


def _parse_multiple_json_documents(stdout: str) -> list[dict]:
    decoder = json.JSONDecoder()
    docs: list[dict] = []
    index = 0
    text = stdout.strip()
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        payload, next_index = decoder.raw_decode(text, index)
        docs.append(payload)
        index = next_index
    return docs


def test_resolve_company_includes_market_specific_next_commands():
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        sec_user_agent="company-intelligence-store test@example.com",
    )
    uk_record = GLEIFRecord(
        lei="213800VCU9TBANZIN455",
        legal_name="THE GYM GROUP PLC",
        country="GB",
        jurisdiction="GB",
        city="LONDON",
        registered_as="08528493",
        status="ACTIVE",
        registration_status="ISSUED",
        other_names=[],
        isins=["GB00BZBX0P70"],
    )
    us_record = SECTickerRecord(
        cik="0001742692",
        ticker="INMD",
        title="InMode Ltd.",
    )
    us_profile = {
        "0001742692": {
            "cik": "0001742692",
            "company_name": "InMode Ltd.",
            "tickers": ["INMD"],
            "exchanges": ["Nasdaq"],
            "entity_type": "other",
            "filing_family": "foreign_private_issuer",
            "suggested_forms": ["20-F", "6-K"],
            "recent_forms_sample": ["6-K", "20-F"],
        }
    }

    with (
        patch("research_platform.cli.get_settings", return_value=settings),
        patch("research_platform.cli.GLEIFClient.search_company", return_value=[uk_record]),
        patch("research_platform.cli.GLEIFClient.get_isins", return_value=["GB00BZBX0P70"]),
        patch("research_platform.cli.SECTickerClient.search_company", return_value=[us_record]),
        patch("research_platform.cli._build_us_profiles", return_value=us_profile),
    ):
        result = runner.invoke(app, ["resolve-company", "Inmode"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)

    assert payload["uk_candidates"][0]["next_commands"][0]["command"].startswith(
        "research ingest-nsm-company --lei 213800VCU9TBANZIN455"
    )
    assert payload["us_candidates"][0]["edgar_profile"]["filing_family"] == "foreign_private_issuer"
    assert payload["us_candidates"][0]["next_commands"][0]["command"] == (
        "research ingest-edgar-filings --cik 0001742692 --forms 20-F,6-K --limit 10 --download --no-persist"
    )


def test_ingest_edgar_filings_warns_when_requested_forms_do_not_match_filer_family():
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        sec_user_agent="company-intelligence-store test@example.com",
    )
    filer_profile = EdgarFilerProfile(
        cik="0001742692",
        company_name="InMode Ltd.",
        tickers=["INMD"],
        exchanges=["Nasdaq"],
        entity_type="other",
        filing_family="foreign_private_issuer",
        suggested_forms=["20-F", "6-K"],
        recent_forms_sample=["6-K", "20-F"],
    )
    submissions = EdgarCompanySubmissions(
        cik="0001742692",
        company_name="InMode Ltd.",
        tickers=["INMD"],
        exchanges=["Nasdaq"],
        filings=[],
    )

    with (
        patch("research_platform.cli.get_settings", return_value=settings),
        patch("research_platform.cli.EdgarClient.inspect_filer", return_value=filer_profile),
        patch("research_platform.cli.EdgarClient.discover_filings", return_value=submissions),
    ):
        result = runner.invoke(
            app,
            [
                "ingest-edgar-filings",
                "--cik",
                "0001742692",
                "--forms",
                "10-K,10-Q,8-K",
                "--no-persist",
            ],
        )

    assert result.exit_code == 0
    payloads = _parse_multiple_json_documents(result.stdout)
    warning_payload, final_payload = payloads

    assert warning_payload["edgar_form_guidance"]["filing_family"] == "foreign_private_issuer"
    assert warning_payload["edgar_form_guidance"]["suggested_forms"] == ["20-F", "6-K"]
    assert final_payload["edgar_form_guidance"]["forms_match"] is False
