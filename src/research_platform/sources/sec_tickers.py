from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from research_platform.core.config import Settings
from research_platform.core.logging import get_logger

logger = get_logger(__name__)


class SECTickerError(RuntimeError):
    """Raised when SEC ticker resolution fails."""


@dataclass(slots=True)
class SECTickerRecord:
    cik: str
    ticker: str
    title: str


class SECTickerClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def search_company(self, name: str, *, limit: int = 10) -> list[SECTickerRecord]:
        payload = self._fetch_company_tickers()
        records = _extract_records(payload)
        ranked = sorted(
            records,
            key=lambda record: _score_record(query=name, record=record),
            reverse=True,
        )
        filtered = [
            record
            for record in ranked
            if _score_record(query=name, record=record)[0] > 0
        ]
        if not filtered:
            raise SECTickerError(f"No usable SEC company ticker record found for {name!r}")
        return filtered[:limit]

    def resolve_company(self, name: str) -> SECTickerRecord:
        return self.search_company(name, limit=1)[0]

    def _fetch_company_tickers(self) -> dict[str, Any]:
        headers = {"User-Agent": self.settings.sec_user_agent}
        try:
            response = httpx.get(
                self.settings.sec_company_tickers_url,
                headers=headers,
                timeout=20,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SECTickerError(f"SEC company tickers request failed: {exc}") from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise SECTickerError("Unexpected SEC company tickers response shape.")
        return payload


def _extract_records(payload: dict[str, Any]) -> list[SECTickerRecord]:
    records: list[SECTickerRecord] = []
    for value in payload.values():
        if not isinstance(value, dict):
            continue
        cik = value.get("cik_str")
        ticker = value.get("ticker")
        title = value.get("title")
        if cik is None or not isinstance(ticker, str) or not isinstance(title, str):
            continue
        records.append(
            SECTickerRecord(
                cik=f"{int(cik):010d}",
                ticker=ticker,
                title=title,
            )
        )
    return records


def _score_record(*, query: str, record: SECTickerRecord) -> tuple[int, int, int]:
    normalized_query = _normalize_text(query, remove_generic_terms=True)
    normalized_title = _normalize_text(record.title, remove_generic_terms=True)
    broad_query = _normalize_text(query, remove_generic_terms=False)
    broad_title = _normalize_text(record.title, remove_generic_terms=False)

    if broad_query and broad_title == broad_query:
        name_score = 5
    elif normalized_query and normalized_title == normalized_query:
        name_score = 4
    elif len(broad_query) >= 4 and broad_query in broad_title:
        name_score = 3
    elif len(normalized_query) >= 3 and normalized_query in normalized_title:
        name_score = 2
    else:
        name_score = 0

    common_suffix_penalty = 0 if any(
        normalized_title.endswith(suffix)
        for suffix in ("inc", "corp", "corporation", "plc", "ltd", "limited", "holdings")
    ) else 1
    ticker_score = 1 if record.ticker.isalpha() and len(record.ticker) <= 5 else 0
    return (name_score, ticker_score, common_suffix_penalty)


def _normalize_text(value: str, *, remove_generic_terms: bool) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else " " for character in value)
    stop_words = {
        "the",
        "class",
        "a",
        "c",
        "common",
        "inc",
        "corp",
        "corporation",
        "plc",
        "ltd",
        "limited",
        "ag",
        "sa",
        "nv",
        "adr",
    }
    if remove_generic_terms:
        stop_words = stop_words | {
            "group",
            "holdings",
            "holding",
            "company",
            "co",
        }
    tokens = [token for token in cleaned.split() if token and token not in stop_words]
    return " ".join(tokens)
