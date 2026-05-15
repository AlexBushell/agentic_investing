from __future__ import annotations

from typing import Optional

import httpx
from pydantic import BaseModel

from research_platform.core.logging import get_logger

logger = get_logger(__name__)

_OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

_YAHOO_SUFFIX: dict[str, str] = {
    "LN": ".L",    # London Stock Exchange (GBP-quoted)
    "X9": ".L",    # London Stock Exchange (GBp pence-quoted, e.g. most UK equities)
    "XH": ".L",    # London Stock Exchange (SETSqx, pence-quoted)
    "US": "",      # NYSE
    "UW": "",      # NASDAQ NMS
    "UA": "",      # NYSE American
    "AT": ".AX",   # ASX
    "FP": ".PA",   # Euronext Paris
    "GR": ".DE",   # XETRA / Frankfurt
    "NA": ".AS",   # Euronext Amsterdam
    "SW": ".SW",   # SIX Swiss Exchange
    "JP": ".T",    # Tokyo
    "HK": ".HK",   # Hong Kong
    "TT": ".TO",   # Toronto
    "SM": ".MC",   # Madrid
    "IM": ".MI",   # Milan
}

# ISIN country prefix → preferred OpenFIGI exchange code.
# Used to request the primary domestic listing directly rather than
# getting OTC/composite instruments (exchCode "XX") by default.
_ISIN_COUNTRY_EXCHANGE: dict[str, str] = {
    "GB": "X9",   # Most UK equities trade in pence on LSE (X9); LN is the fallback
    "US": "US",
    "AU": "AT",
    "DE": "GR",
    "FR": "FP",
    "JP": "JP",
    "HK": "HK",
    "CA": "TT",
    "ES": "SM",
    "IT": "IM",
    "NL": "NA",
    "CH": "SW",
}


class OpenFIGIError(RuntimeError):
    """Raised when an OpenFIGI lookup fails or returns no usable result."""


class OpenFIGIResult(BaseModel):
    figi: str
    composite_figi: Optional[str] = None
    share_class_figi: Optional[str] = None
    name: str
    ticker: str
    exch_code: str
    security_type: Optional[str] = None
    market_sector: Optional[str] = None


def to_yahoo_ticker(ticker: str, exch_code: str) -> str:
    """Derive a Yahoo Finance ticker from an OpenFIGI ticker and exchange code."""
    return ticker + _YAHOO_SUFFIX.get(exch_code, "")


class OpenFIGIClient:
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def lookup_isin(self, isin: str) -> OpenFIGIResult:
        """Return the best-matching equity result for an ISIN.

        Tries the primary domestic exchange for the ISIN's country first
        (e.g. LN for GB ISINs), then falls back to an unfiltered search
        if that returns nothing.
        """
        preferred_exch = _ISIN_COUNTRY_EXCHANGE.get(isin[:2].upper())

        if preferred_exch:
            result = self._query(isin, exch_code=preferred_exch)
            if result is not None:
                return result
            logger.debug(
                "No result for %s on preferred exchange %s, falling back to unfiltered",
                isin, preferred_exch,
            )

        result = self._query(isin, exch_code=None)
        if result is None:
            raise OpenFIGIError(f"No usable equity result in OpenFIGI for ISIN {isin!r}")
        return result

    def _query(self, isin: str, exch_code: Optional[str]) -> Optional[OpenFIGIResult]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key

        job: dict = {"idType": "ID_ISIN", "idValue": isin}
        if exch_code:
            job["exchCode"] = exch_code

        try:
            response = httpx.post(
                _OPENFIGI_URL,
                json=[job],
                headers=headers,
                timeout=15,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OpenFIGIError(f"OpenFIGI request failed for ISIN {isin!r}: {exc}") from exc

        payload = response.json()
        if not payload:
            raise OpenFIGIError(f"Empty response from OpenFIGI for ISIN {isin!r}")

        first = payload[0]
        if "error" in first:
            raise OpenFIGIError(f"OpenFIGI error for ISIN {isin!r}: {first['error']}")
        if "warning" in first or "data" not in first:
            return None

        result = _select_best(first["data"])
        if result is not None:
            logger.info(
                "OpenFIGI resolved %s → %s (%s:%s)",
                isin, result.name, result.exch_code, result.ticker,
            )
        return result


def _select_best(entries: list[dict]) -> Optional[OpenFIGIResult]:
    """Pick the most relevant equity entry from a list of OpenFIGI results."""
    if not entries:
        return None

    def _score(entry: dict) -> tuple[int, int]:
        sec_type = (entry.get("securityType") or "").lower()
        sec_type2 = (entry.get("securityType2") or "").lower()
        type_score = (
            2 if ("common" in sec_type or "ordinary" in sec_type2) else
            1 if (entry.get("marketSector") or "").lower() == "equity" else
            0
        )
        exch_code = entry.get("exchCode", "")
        # XX = OTC/composite, strongly deprioritise; known exchange = 2; unknown = 1
        exch_score = 0 if exch_code == "XX" else 2 if exch_code in _YAHOO_SUFFIX else 1
        return (type_score, exch_score)

    best = max(entries, key=_score)
    return OpenFIGIResult(
        figi=best.get("figi", ""),
        composite_figi=best.get("compositeFIGI"),
        share_class_figi=best.get("shareClassFIGI"),
        name=best.get("name", ""),
        ticker=best.get("ticker", ""),
        exch_code=best.get("exchCode", ""),
        security_type=best.get("securityType"),
        market_sector=best.get("marketSector"),
    )
