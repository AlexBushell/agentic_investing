from unittest.mock import MagicMock, patch

import pytest

from research_platform.core.config import Settings
from research_platform.sources.sec_tickers import SECTickerClient, SECTickerError, SECTickerRecord


SEC_TICKERS_RESPONSE = {
    "0": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
    "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "2": {"cik_str": 1841666, "ticker": "ARMK", "title": "Aramark"},
    "3": {"cik_str": 1610520, "ticker": "UBS", "title": "UBS Group AG"},
}


def make_mock_response(json_data, status_code=200):
    response = MagicMock()
    response.json.return_value = json_data
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    return response


def make_settings() -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        sec_user_agent="company-intelligence-store test@example.com",
    )


class TestSECTickerClient:
    def test_search_company_returns_ranked_candidates(self):
        with patch("research_platform.sources.sec_tickers.httpx.get") as mock_get:
            mock_get.return_value = make_mock_response(SEC_TICKERS_RESPONSE)
            results = SECTickerClient(make_settings()).search_company("Alphabet", limit=5)
        assert len(results) >= 1
        assert isinstance(results[0], SECTickerRecord)
        assert results[0].ticker == "GOOGL"
        assert results[0].cik == "0001652044"

    def test_search_company_uses_sec_user_agent_and_url(self):
        settings = make_settings()
        with patch("research_platform.sources.sec_tickers.httpx.get") as mock_get:
            mock_get.return_value = make_mock_response(SEC_TICKERS_RESPONSE)
            SECTickerClient(settings).search_company("Alphabet", limit=5)
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["User-Agent"] == settings.sec_user_agent
        assert kwargs["timeout"] == 20

    def test_resolve_company_returns_best_match(self):
        with patch("research_platform.sources.sec_tickers.httpx.get") as mock_get:
            mock_get.return_value = make_mock_response(SEC_TICKERS_RESPONSE)
            result = SECTickerClient(make_settings()).resolve_company("Apple")
        assert result.ticker == "AAPL"
        assert result.cik == "0000320193"

    def test_search_company_matches_full_string_substring(self):
        with patch("research_platform.sources.sec_tickers.httpx.get") as mock_get:
            mock_get.return_value = make_mock_response(SEC_TICKERS_RESPONSE)
            results = SECTickerClient(make_settings()).search_company("Alphabet Inc", limit=5)
        assert results[0].ticker == "GOOGL"

    def test_search_company_raises_when_no_match(self):
        with patch("research_platform.sources.sec_tickers.httpx.get") as mock_get:
            mock_get.return_value = make_mock_response(SEC_TICKERS_RESPONSE)
            with pytest.raises(SECTickerError, match="No usable SEC company ticker record"):
                SECTickerClient(make_settings()).search_company("Definitely Unknown Company", limit=5)

    def test_search_company_http_error_raises_sec_ticker_error(self):
        import httpx

        with patch("research_platform.sources.sec_tickers.httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("connection refused")
            with pytest.raises(SECTickerError, match="SEC company tickers request failed"):
                SECTickerClient(make_settings()).search_company("Alphabet", limit=5)

    def test_search_company_ignores_generic_group_suffix_matches(self):
        with patch("research_platform.sources.sec_tickers.httpx.get") as mock_get:
            mock_get.return_value = make_mock_response(SEC_TICKERS_RESPONSE)
            with pytest.raises(SECTickerError, match="No usable SEC company ticker record"):
                SECTickerClient(make_settings()).search_company("The Gym Group", limit=5)

    def test_search_company_ignores_short_collapsed_query_matches(self):
        with patch("research_platform.sources.sec_tickers.httpx.get") as mock_get:
            mock_get.return_value = make_mock_response(SEC_TICKERS_RESPONSE)
            with pytest.raises(SECTickerError, match="No usable SEC company ticker record"):
                SECTickerClient(make_settings()).search_company("IG Group", limit=5)
