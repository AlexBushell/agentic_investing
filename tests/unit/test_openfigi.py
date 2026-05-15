from unittest.mock import MagicMock, patch

import pytest

from research_platform.sources.openfigi import (
    OpenFIGIClient,
    OpenFIGIError,
    OpenFIGIResult,
    _select_best,
    to_yahoo_ticker,
    _ISIN_COUNTRY_EXCHANGE,
)


# ---------------------------------------------------------------------------
# to_yahoo_ticker
# ---------------------------------------------------------------------------


class TestToYahooTicker:
    def test_london_stock_exchange(self):
        assert to_yahoo_ticker("TSCO", "LN") == "TSCO.L"

    def test_nyse_no_suffix(self):
        assert to_yahoo_ticker("AAPL", "US") == "AAPL"

    def test_nasdaq_no_suffix(self):
        assert to_yahoo_ticker("MSFT", "UW") == "MSFT"

    def test_asx(self):
        assert to_yahoo_ticker("CBA", "AT") == "CBA.AX"

    def test_euronext_paris(self):
        assert to_yahoo_ticker("AIR", "FP") == "AIR.PA"

    def test_xetra(self):
        assert to_yahoo_ticker("BMW", "GR") == "BMW.DE"

    def test_unknown_exchange_no_suffix(self):
        assert to_yahoo_ticker("XYZ", "UNKNOWN") == "XYZ"


# ---------------------------------------------------------------------------
# _select_best
# ---------------------------------------------------------------------------

COMMON_ENTRY = {
    "figi": "BBG000BH0JM9",
    "compositeFIGI": "BBG000BVLR80",
    "shareClassFIGI": "BBG001S93ZQ7",
    "name": "TESCO PLC",
    "ticker": "TSCO",
    "exchCode": "LN",
    "securityType": "Common Stock",
    "securityType2": "Common Stock",
    "marketSector": "Equity",
}


class TestSelectBest:
    def test_returns_none_for_empty(self):
        assert _select_best([]) is None

    def test_single_entry_returned(self):
        result = _select_best([COMMON_ENTRY])
        assert isinstance(result, OpenFIGIResult)
        assert result.ticker == "TSCO"
        assert result.exch_code == "LN"
        assert result.name == "TESCO PLC"

    def test_prefers_common_stock_over_preferred(self):
        preferred = {**COMMON_ENTRY, "securityType": "Preferred", "figi": "BBG000AAA"}
        common = {**COMMON_ENTRY, "securityType": "Common Stock", "figi": "BBG000BBB"}
        result = _select_best([preferred, common])
        assert result.figi == "BBG000BBB"

    def test_prefers_known_exchange_over_unknown(self):
        unknown_exch = {**COMMON_ENTRY, "exchCode": "ZZUNK", "figi": "BBG000AAA"}
        known_exch = {**COMMON_ENTRY, "exchCode": "LN", "figi": "BBG000BBB"}
        result = _select_best([unknown_exch, known_exch])
        assert result.figi == "BBG000BBB"

    def test_xx_exchange_loses_to_known_exchange(self):
        otc = {**COMMON_ENTRY, "exchCode": "XX", "ticker": "TSCOUSD", "figi": "BBG000AAA"}
        primary = {**COMMON_ENTRY, "exchCode": "LN", "ticker": "TSCO", "figi": "BBG000BBB"}
        result = _select_best([otc, primary])
        assert result.figi == "BBG000BBB"
        assert result.ticker == "TSCO"

    def test_xx_exchange_loses_to_unknown_exchange(self):
        otc = {**COMMON_ENTRY, "exchCode": "XX", "figi": "BBG000AAA"}
        other = {**COMMON_ENTRY, "exchCode": "ZZUNK", "figi": "BBG000BBB"}
        result = _select_best([otc, other])
        assert result.figi == "BBG000BBB"

    def test_composite_figi_and_share_class_mapped(self):
        result = _select_best([COMMON_ENTRY])
        assert result.composite_figi == "BBG000BVLR80"
        assert result.share_class_figi == "BBG001S93ZQ7"


# ---------------------------------------------------------------------------
# OpenFIGIClient.lookup_isin
# ---------------------------------------------------------------------------

TESCO_RESPONSE = [{"data": [COMMON_ENTRY]}]
NOT_FOUND_RESPONSE = [{"warning": "No identifier found."}]
ERROR_RESPONSE = [{"error": "Invalid request."}]


def make_mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


def mock_post_preferred_hit(json_data):
    """Simulate: preferred exchange returns data on first call."""
    return MagicMock(side_effect=[make_mock_response(json_data)])


def mock_post_preferred_miss_then_hit(fallback_data):
    """Simulate: preferred exchange returns warning, fallback returns data."""
    return MagicMock(side_effect=[
        make_mock_response([{"warning": "No identifier found."}]),
        make_mock_response(fallback_data),
    ])


class TestISINCountryMapping:
    def test_gb_maps_to_x9(self):
        assert _ISIN_COUNTRY_EXCHANGE["GB"] == "X9"

    def test_us_maps_to_us(self):
        assert _ISIN_COUNTRY_EXCHANGE["US"] == "US"

    def test_au_maps_to_at(self):
        assert _ISIN_COUNTRY_EXCHANGE["AU"] == "AT"


class TestOpenFIGIClientLookup:
    def test_successful_lookup_on_preferred_exchange(self):
        with patch("research_platform.sources.openfigi.httpx.post") as mock_post:
            mock_post.return_value = make_mock_response(TESCO_RESPONSE)
            result = OpenFIGIClient().lookup_isin("GB0008847096")
        assert result.name == "TESCO PLC"
        assert result.ticker == "TSCO"
        assert result.exch_code == "LN"

    def test_preferred_exchange_filter_sent_for_gb_isin(self):
        with patch("research_platform.sources.openfigi.httpx.post") as mock_post:
            mock_post.return_value = make_mock_response(TESCO_RESPONSE)
            OpenFIGIClient().lookup_isin("GB0008847096")
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == [{"idType": "ID_ISIN", "idValue": "GB0008847096", "exchCode": "X9"}]

    def test_falls_back_to_unfiltered_when_preferred_misses(self):
        with patch("research_platform.sources.openfigi.httpx.post") as mock_post:
            mock_post.side_effect = [
                make_mock_response(NOT_FOUND_RESPONSE),
                make_mock_response(TESCO_RESPONSE),
            ]
            result = OpenFIGIClient().lookup_isin("GB0008847096")
        assert mock_post.call_count == 2
        assert result.ticker == "TSCO"

    def test_no_preferred_exchange_for_unknown_country(self):
        """ISIN with unknown country prefix goes straight to unfiltered search."""
        with patch("research_platform.sources.openfigi.httpx.post") as mock_post:
            mock_post.return_value = make_mock_response(TESCO_RESPONSE)
            OpenFIGIClient().lookup_isin("ZZ0008847096")
        _, kwargs = mock_post.call_args
        assert "exchCode" not in kwargs["json"][0]

    def test_api_key_sent_in_header(self):
        with patch("research_platform.sources.openfigi.httpx.post") as mock_post:
            mock_post.return_value = make_mock_response(TESCO_RESPONSE)
            OpenFIGIClient(api_key="test-key").lookup_isin("GB0008847096")
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["X-OPENFIGI-APIKEY"] == "test-key"

    def test_no_api_key_omits_header(self):
        with patch("research_platform.sources.openfigi.httpx.post") as mock_post:
            mock_post.return_value = make_mock_response(TESCO_RESPONSE)
            OpenFIGIClient(api_key="").lookup_isin("GB0008847096")
        _, kwargs = mock_post.call_args
        assert "X-OPENFIGI-APIKEY" not in kwargs["headers"]

    def test_both_queries_exhausted_raises_error(self):
        with patch("research_platform.sources.openfigi.httpx.post") as mock_post:
            mock_post.return_value = make_mock_response(NOT_FOUND_RESPONSE)
            with pytest.raises(OpenFIGIError, match="No usable equity result"):
                OpenFIGIClient().lookup_isin("GB0000000000")

    def test_api_error_raises_error(self):
        with patch("research_platform.sources.openfigi.httpx.post") as mock_post:
            mock_post.return_value = make_mock_response(ERROR_RESPONSE)
            with pytest.raises(OpenFIGIError, match="OpenFIGI error"):
                OpenFIGIClient().lookup_isin("GB0000000000")

    def test_http_error_raises_openfigi_error(self):
        import httpx
        with patch("research_platform.sources.openfigi.httpx.post") as mock_post:
            mock_post.side_effect = httpx.ConnectError("connection refused")
            with pytest.raises(OpenFIGIError, match="request failed"):
                OpenFIGIClient().lookup_isin("GB0008847096")
