from unittest.mock import MagicMock, patch

import pytest

from research_platform.sources.market import (
    AnnualFinancials,
    FinancialHistory,
    MarketDataError,
    MarketSnapshot,
    YFinanceClient,
    _coerce,
    _margin,
    _row,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestCoerce:
    def test_none_returns_none(self):
        assert _coerce(None) is None

    def test_nan_returns_none(self):
        import math
        assert _coerce(float("nan")) is None

    def test_int_converted(self):
        assert _coerce(1000) == 1000.0

    def test_float_passthrough(self):
        assert _coerce(3.14) == pytest.approx(3.14)

    def test_string_returns_none(self):
        assert _coerce("n/a") is None


class TestMargin:
    def test_normal_calculation(self):
        assert _margin(500.0, 1000.0) == pytest.approx(0.5)

    def test_none_numerator(self):
        assert _margin(None, 1000.0) is None

    def test_none_denominator(self):
        assert _margin(500.0, None) is None

    def test_zero_denominator(self):
        assert _margin(500.0, 0.0) is None

    def test_rounded_to_4dp(self):
        assert _margin(1.0, 3.0) == pytest.approx(0.3333)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_df(data: dict):
    """Build a minimal DataFrame mock where df.loc[key, col] returns data[key]."""
    import pandas as pd
    import numpy as np
    dates = [pd.Timestamp("2025-03-31"), pd.Timestamp("2024-03-31"),
             pd.Timestamp("2023-03-31"), pd.Timestamp("2022-03-31")]
    rows = {}
    for key, values in data.items():
        rows[key] = {d: v for d, v in zip(dates, values)}
    return pd.DataFrame(rows).T


INCOME_DATA = {
    "Total Revenue":     [1_000_000, 950_000, 900_000, 850_000],
    "Gross Profit":      [300_000,   280_000, 260_000, 240_000],
    "Operating Income":  [100_000,    90_000,  85_000,  80_000],
    "Net Income":         [70_000,    60_000,  55_000,  50_000],
}

CASHFLOW_DATA = {
    "Free Cash Flow":    [80_000,  75_000,  70_000,  65_000],
    "Operating Cash Flow": [120_000, 110_000, 105_000, 100_000],
    "Capital Expenditure": [-40_000, -35_000, -35_000, -35_000],
}

BALANCE_DATA = {
    "Total Debt":                       [500_000, 480_000, 460_000, 440_000],
    "Cash And Cash Equivalents":         [100_000,  90_000,  85_000,  80_000],
}

INFO = {
    "longName": "Test Company PLC",
    "currency": "GBP",
    "currentPrice": 250.0,
    "marketCap": 10_000_000,
    "enterpriseValue": 10_400_000,
    "sharesOutstanding": 40_000,
    "fiftyTwoWeekHigh": 300.0,
    "fiftyTwoWeekLow": 200.0,
}


def make_ticker_mock():
    import pandas as pd
    t = MagicMock()
    t.info = INFO
    t.income_stmt = make_df(INCOME_DATA)
    t.cash_flow = make_df(CASHFLOW_DATA)
    t.balance_sheet = make_df(BALANCE_DATA)
    return t


# ---------------------------------------------------------------------------
# MarketSnapshot
# ---------------------------------------------------------------------------


class TestMarketSnapshot:
    def test_snapshot_fields_populated(self):
        with patch("research_platform.sources.market.yf") as mock_yf:
            mock_yf.Ticker.return_value = make_ticker_mock()
            snapshot, _ = YFinanceClient().get_snapshot("TST.L")

        assert snapshot.ticker == "TST.L"
        assert snapshot.name == "Test Company PLC"
        assert snapshot.currency == "GBP"
        assert snapshot.price == pytest.approx(250.0)
        assert snapshot.market_cap == 10_000_000
        assert snapshot.enterprise_value == 10_400_000
        assert snapshot.shares_outstanding == 40_000
        assert snapshot.week_52_high == pytest.approx(300.0)
        assert snapshot.week_52_low == pytest.approx(200.0)

    def test_gbp_pence_normalised_to_pounds(self):
        pence_info = {
            **INFO,
            "currency": "GBp",
            "currentPrice": 449.8,
            "fiftyTwoWeekHigh": 508.2,
            "fiftyTwoWeekLow": 362.9,
        }
        t = make_ticker_mock()
        t.info = pence_info
        with patch("research_platform.sources.market.yf") as mock_yf:
            mock_yf.Ticker.return_value = t
            snapshot, _ = YFinanceClient().get_snapshot("TST.L")
        assert snapshot.currency == "GBP"
        assert snapshot.price == pytest.approx(4.498)
        assert snapshot.week_52_high == pytest.approx(5.082)
        assert snapshot.week_52_low == pytest.approx(3.629)

    def test_raises_on_empty_info(self):
        with patch("research_platform.sources.market.yf") as mock_yf:
            t = MagicMock()
            t.info = {}
            mock_yf.Ticker.return_value = t
            with pytest.raises(MarketDataError):
                YFinanceClient().get_snapshot("BAD.L")


# ---------------------------------------------------------------------------
# FinancialHistory
# ---------------------------------------------------------------------------


class TestFinancialHistory:
    def _get_history(self):
        with patch("research_platform.sources.market.yf") as mock_yf:
            mock_yf.Ticker.return_value = make_ticker_mock()
            _, history = YFinanceClient().get_snapshot("TST.L")
        return history

    def test_returns_four_years(self):
        history = self._get_history()
        assert len(history.years) == 4

    def test_newest_year_first(self):
        history = self._get_history()
        assert history.years[0].period_end == "2025-03-31"
        assert history.years[1].period_end == "2024-03-31"

    def test_revenue_populated(self):
        history = self._get_history()
        assert history.years[0].revenue == pytest.approx(1_000_000)

    def test_gross_margin_calculated(self):
        history = self._get_history()
        assert history.years[0].gross_margin == pytest.approx(0.3)

    def test_operating_margin_calculated(self):
        history = self._get_history()
        assert history.years[0].operating_margin == pytest.approx(0.1)

    def test_fcf_from_direct_field(self):
        history = self._get_history()
        assert history.years[0].free_cash_flow == pytest.approx(80_000)

    def test_net_debt_calculated(self):
        history = self._get_history()
        assert history.years[0].net_debt == pytest.approx(400_000)

    def test_fcf_fallback_to_ocf_minus_capex(self):
        import pandas as pd
        cashflow_no_fcf = make_df({
            k: v for k, v in CASHFLOW_DATA.items() if k != "Free Cash Flow"
        })
        t = make_ticker_mock()
        t.cash_flow = cashflow_no_fcf
        with patch("research_platform.sources.market.yf") as mock_yf:
            mock_yf.Ticker.return_value = t
            _, history = YFinanceClient().get_snapshot("TST.L")
        assert history.years[0].free_cash_flow == pytest.approx(80_000)  # 120k - 40k

    def test_empty_income_stmt_returns_empty_history(self):
        import pandas as pd
        t = make_ticker_mock()
        t.income_stmt = pd.DataFrame()
        with patch("research_platform.sources.market.yf") as mock_yf:
            mock_yf.Ticker.return_value = t
            _, history = YFinanceClient().get_snapshot("TST.L")
        assert history.years == []
