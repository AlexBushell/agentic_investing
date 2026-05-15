from __future__ import annotations

import math
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from research_platform.core.logging import get_logger

logger = get_logger(__name__)

try:
    import yfinance as yf
except ImportError:
    yf = None  # type: ignore


class MarketDataError(RuntimeError):
    """Raised when market data cannot be retrieved or is unusable."""


class MarketSnapshot(BaseModel):
    ticker: str
    name: Optional[str] = None
    currency: str = "USD"
    price: Optional[float] = None
    market_cap: Optional[float] = None
    enterprise_value: Optional[float] = None
    shares_outstanding: Optional[float] = None
    week_52_high: Optional[float] = None
    week_52_low: Optional[float] = None
    as_of: str = Field(default_factory=lambda: date.today().isoformat())


class AnnualFinancials(BaseModel):
    period_end: str
    revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    operating_profit: Optional[float] = None
    net_income: Optional[float] = None
    free_cash_flow: Optional[float] = None
    net_debt: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    fcf_margin: Optional[float] = None


class FinancialHistory(BaseModel):
    ticker: str
    currency: str = "USD"
    years: list[AnnualFinancials] = Field(default_factory=list)


class YFinanceClient:
    def get_snapshot(self, ticker: str) -> tuple[MarketSnapshot, FinancialHistory]:
        """Fetch current market data and up to 4 years of annual financials."""
        if yf is None:
            raise MarketDataError("yfinance is not installed. Run: pip install yfinance")

        t = yf.Ticker(ticker)
        info = t.info or {}

        if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
            raise MarketDataError(
                f"No market data returned for {ticker!r}. "
                "Check the ticker is in Yahoo Finance format (e.g. TSCO.L)."
            )

        raw_currency = info.get("currency", "USD")
        # Yahoo quotes LSE prices in pence (GBp) but reports market cap/EV in pounds.
        # Normalise price to the base currency so all figures are on the same scale.
        price_raw = _coerce(info.get("currentPrice") or info.get("regularMarketPrice"))
        if raw_currency == "GBp":
            currency = "GBP"
            divisor = 100.0
        else:
            currency = raw_currency
            divisor = 1.0

        def _pence(v) -> Optional[float]:
            val = _coerce(v)
            return round(val / divisor, 4) if val is not None else None

        snapshot = MarketSnapshot(
            ticker=ticker,
            name=info.get("longName") or info.get("shortName"),
            currency=currency,
            price=_pence(info.get("currentPrice") or info.get("regularMarketPrice")),
            market_cap=_coerce(info.get("marketCap")),
            enterprise_value=_coerce(info.get("enterpriseValue")),
            shares_outstanding=_coerce(info.get("sharesOutstanding")),
            week_52_high=_pence(info.get("fiftyTwoWeekHigh")),
            week_52_low=_pence(info.get("fiftyTwoWeekLow")),
        )

        history = self._build_history(ticker, currency, t)  # currency already normalised
        logger.info(
            "Fetched market data for %s: price=%s cap=%s years=%d",
            ticker, snapshot.price, snapshot.market_cap, len(history.years),
        )
        return snapshot, history

    def _build_history(self, ticker: str, currency: str, t) -> FinancialHistory:
        try:
            income = t.income_stmt
            cash_flow = t.cash_flow
            balance = t.balance_sheet
        except Exception as exc:
            logger.warning("Could not retrieve financials for %s: %s", ticker, exc)
            return FinancialHistory(ticker=ticker, currency=currency)

        if income is None or income.empty:
            return FinancialHistory(ticker=ticker, currency=currency)

        years: list[AnnualFinancials] = []
        for col in income.columns[:4]:  # newest first, max 4 years
            period_end = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)

            revenue = _row(income, col, "Total Revenue", "Revenue")
            gross_profit = _row(income, col, "Gross Profit")
            op_profit = _row(income, col, "Operating Income", "EBIT", "Operating Profit")
            net_income = _row(income, col, "Net Income")

            fcf = _row(cash_flow, col, "Free Cash Flow") if cash_flow is not None and not cash_flow.empty else None
            if fcf is None:
                ocf = _row(cash_flow, col, "Operating Cash Flow")
                capex = _row(cash_flow, col, "Capital Expenditure")
                if ocf is not None and capex is not None:
                    fcf = ocf + capex  # capex is negative in yfinance

            total_debt = _row(balance, col, "Total Debt", "Long Term Debt") if balance is not None and not balance.empty else None
            cash = _row(balance, col, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
            net_debt = (total_debt - cash) if (total_debt is not None and cash is not None) else None

            years.append(AnnualFinancials(
                period_end=period_end,
                revenue=revenue,
                gross_profit=gross_profit,
                operating_profit=op_profit,
                net_income=net_income,
                free_cash_flow=fcf,
                net_debt=net_debt,
                gross_margin=_margin(gross_profit, revenue),
                operating_margin=_margin(op_profit, revenue),
                fcf_margin=_margin(fcf, revenue),
            ))

        return FinancialHistory(ticker=ticker, currency=currency, years=years)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce(value) -> Optional[float]:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _row(df, col, *keys) -> Optional[float]:
    for key in keys:
        try:
            import math
            val = df.loc[key, col]
            if val is None or (isinstance(val, float) and math.isnan(val)):
                continue
            return float(val)
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _margin(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator, 4)
