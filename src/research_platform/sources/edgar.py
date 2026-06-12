from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, Field

from research_platform.core.config import Settings
from research_platform.core.logging import get_logger

logger = get_logger(__name__)


class EdgarError(RuntimeError):
    """Raised when EDGAR discovery or download fails."""


class EdgarFiling(BaseModel):
    cik: str
    company_name: str
    form: str
    filing_date: str
    accession_number: str
    primary_document: str
    report_date: Optional[str] = None
    primary_doc_description: Optional[str] = None
    is_xbrl: Optional[bool] = None
    filing_href: str


class EdgarCompanySubmissions(BaseModel):
    cik: str
    company_name: str
    tickers: list[str] = Field(default_factory=list)
    exchanges: list[str] = Field(default_factory=list)
    filings: list[EdgarFiling] = Field(default_factory=list)


class EdgarFilerProfile(BaseModel):
    cik: str
    company_name: str
    tickers: list[str] = Field(default_factory=list)
    exchanges: list[str] = Field(default_factory=list)
    entity_type: Optional[str] = None
    filing_family: str
    suggested_forms: list[str] = Field(default_factory=list)
    recent_forms_sample: list[str] = Field(default_factory=list)


class EdgarAnnualHistoryYear(BaseModel):
    year: int
    selected_filing: EdgarFiling
    alternate_filings: list[EdgarFiling] = Field(default_factory=list)


class EdgarAnnualHistoryDiscoveryResult(BaseModel):
    cik: str
    company_name: str
    tickers: list[str] = Field(default_factory=list)
    exchanges: list[str] = Field(default_factory=list)
    filing_family: str
    annual_forms: list[str] = Field(default_factory=list)
    discovered_filings: list[EdgarFiling] = Field(default_factory=list)
    selected_years: list[EdgarAnnualHistoryYear] = Field(default_factory=list)
    missing_years: list[int] = Field(default_factory=list)


class EdgarClient:
    """Client for SEC public EDGAR submissions data."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def discover_filings(
        self,
        *,
        cik: str,
        forms: tuple[str, ...] = ("10-K", "10-Q", "8-K", "20-F", "40-F", "6-K"),
        limit: int = 20,
    ) -> EdgarCompanySubmissions:
        payload = self._get_json(f"/submissions/CIK{_normalise_cik(cik)}.json")

        company_name = payload.get("name")
        if not company_name:
            raise EdgarError(f"EDGAR submissions payload had no company name for CIK {cik}")

        recent = payload.get("filings", {}).get("recent", {})
        forms_data = recent.get("form", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_documents = recent.get("primaryDocument", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        descriptions = recent.get("primaryDocDescription", [])
        is_xbrl_values = recent.get("isXBRL", [])

        filings: list[EdgarFiling] = []
        allowed_forms = {item.upper() for item in forms}
        row_count = min(
            len(forms_data),
            len(accession_numbers),
            len(primary_documents),
            len(filing_dates),
        )

        for index in range(row_count):
            form = str(forms_data[index]).upper()
            if form not in allowed_forms:
                continue

            accession = str(accession_numbers[index])
            primary_document = str(primary_documents[index])
            filing_href = self._filing_href(cik=cik, accession_number=accession, primary_document=primary_document)
            filings.append(
                EdgarFiling(
                    cik=_normalise_cik(cik),
                    company_name=company_name,
                    form=form,
                    filing_date=str(filing_dates[index]),
                    accession_number=accession,
                    primary_document=primary_document,
                    report_date=_optional_value(report_dates, index),
                    primary_doc_description=_optional_value(descriptions, index),
                    is_xbrl=_optional_bool(is_xbrl_values, index),
                    filing_href=filing_href,
                )
            )
            if len(filings) >= limit:
                break

        return EdgarCompanySubmissions(
            cik=_normalise_cik(cik),
            company_name=company_name,
            tickers=[str(item) for item in payload.get("tickers", []) if item],
            exchanges=[str(item) for item in payload.get("exchanges", []) if item],
            filings=filings,
        )

    def inspect_filer(self, *, cik: str) -> EdgarFilerProfile:
        payload = self._get_json(f"/submissions/CIK{_normalise_cik(cik)}.json")

        company_name = payload.get("name")
        if not company_name:
            raise EdgarError(f"EDGAR submissions payload had no company name for CIK {cik}")

        recent_forms = [
            str(item).upper()
            for item in payload.get("filings", {}).get("recent", {}).get("form", [])
            if item
        ]
        filing_family, suggested_forms = infer_edgar_filing_family(
            entity_type=payload.get("entityType"),
            recent_forms=recent_forms,
        )

        return EdgarFilerProfile(
            cik=_normalise_cik(cik),
            company_name=company_name,
            tickers=[str(item) for item in payload.get("tickers", []) if item],
            exchanges=[str(item) for item in payload.get("exchanges", []) if item],
            entity_type=_optional_scalar(payload.get("entityType")),
            filing_family=filing_family,
            suggested_forms=suggested_forms,
            recent_forms_sample=recent_forms[:10],
        )

    def discover_annual_history(
        self,
        *,
        cik: str,
        years: int = 5,
        limit: int = 100,
    ) -> EdgarAnnualHistoryDiscoveryResult:
        payload = self._get_json(f"/submissions/CIK{_normalise_cik(cik)}.json")

        company_name = payload.get("name")
        if not company_name:
            raise EdgarError(f"EDGAR submissions payload had no company name for CIK {cik}")

        recent_forms = [
            str(item).upper()
            for item in payload.get("filings", {}).get("recent", {}).get("form", [])
            if item
        ]
        filing_family, suggested_forms = infer_edgar_filing_family(
            entity_type=payload.get("entityType"),
            recent_forms=recent_forms,
        )
        annual_forms = [form for form in suggested_forms if form in {"10-K", "20-F", "40-F"}]
        if not annual_forms:
            annual_forms = ["10-K", "20-F", "40-F"]

        submissions = self.discover_filings(cik=cik, forms=tuple(annual_forms), limit=limit)
        selected_years = _group_edgar_annual_filings_by_year(submissions.filings, years=years)
        missing_years = _missing_edgar_years(selected_years, years=years)

        return EdgarAnnualHistoryDiscoveryResult(
            cik=submissions.cik,
            company_name=submissions.company_name,
            tickers=submissions.tickers,
            exchanges=submissions.exchanges,
            filing_family=filing_family,
            annual_forms=annual_forms,
            discovered_filings=submissions.filings,
            selected_years=selected_years,
            missing_years=missing_years,
        )

    def download_filing(self, filing: EdgarFiling) -> Path:
        slug = _slugify(filing.company_name) or filing.cik
        download_dir = self.settings.sec_download_dir / slug
        download_dir.mkdir(parents=True, exist_ok=True)
        target = download_dir / f"{filing.form}_{filing.accession_number.replace('-', '')}_{Path(filing.primary_document).name}"

        with httpx.Client(headers=self._headers(), follow_redirects=True, timeout=60) as client:
            response = client.get(filing.filing_href)
            response.raise_for_status()
            target.write_bytes(response.content)

        logger.info("Downloaded EDGAR filing %s to %s", filing.accession_number, target)
        return target

    def _get_json(self, path: str) -> dict:
        url = f"{self.settings.sec_data_base_url.rstrip('/')}{path}"
        with httpx.Client(headers=self._headers(), follow_redirects=True, timeout=30) as client:
            try:
                response = client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise EdgarError(f"EDGAR request failed for {url}: {exc}") from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise EdgarError(f"Unexpected EDGAR payload type from {url}")
        return payload

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        }

    def _filing_href(self, *, cik: str, accession_number: str, primary_document: str) -> str:
        cik_number = str(int(_normalise_cik(cik)))
        accession_compact = accession_number.replace("-", "")
        return (
            f"{self.settings.sec_archives_base_url.rstrip('/')}/edgar/data/"
            f"{cik_number}/{accession_compact}/{primary_document}"
        )


def _normalise_cik(cik: str) -> str:
    digits = "".join(ch for ch in str(cik) if ch.isdigit())
    if not digits:
        raise EdgarError(f"Invalid CIK: {cik!r}")
    return digits.zfill(10)


def _optional_value(values: list, index: int) -> Optional[str]:
    if index >= len(values):
        return None
    value = values[index]
    if value in (None, ""):
        return None
    return str(value)


def _optional_bool(values: list, index: int) -> Optional[bool]:
    if index >= len(values):
        return None
    value = values[index]
    if value in (None, ""):
        return None
    return bool(value)


def _optional_scalar(value: object) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


def map_edgar_form_to_document_role(form: str) -> str:
    normalized = form.strip().upper()
    mapping = {
        "10-K": "ANNUAL_REPORT",
        "20-F": "ANNUAL_REPORT",
        "40-F": "ANNUAL_REPORT",
        "10-Q": "INTERIM_REPORT",
        "6-K": "TRADING_UPDATE",
        "8-K": "TRADING_UPDATE",
    }
    return mapping.get(normalized, normalized.replace("-", "_"))


def infer_edgar_filing_family(
    *,
    entity_type: Optional[str],
    recent_forms: list[str],
) -> tuple[str, list[str]]:
    normalized_entity_type = (entity_type or "").strip().lower()
    normalized_forms = [form.strip().upper() for form in recent_forms if form]

    foreign_forms = {"20-F", "40-F", "6-K", "F-1", "F-3", "F-4"}
    domestic_forms = {"10-K", "10-Q", "8-K", "S-1", "S-3", "S-4"}

    if normalized_entity_type in {"foreign private issuer", "other"}:
        return ("foreign_private_issuer", ["20-F", "6-K"])

    if any(form in foreign_forms for form in normalized_forms):
        return ("foreign_private_issuer", ["20-F", "6-K"])

    if any(form in domestic_forms for form in normalized_forms):
        return ("domestic_issuer", ["10-K", "10-Q", "8-K"])

    return ("unknown", ["10-K", "10-Q", "8-K", "20-F", "6-K"])


def _group_edgar_annual_filings_by_year(
    filings: list[EdgarFiling],
    *,
    years: int,
) -> list[EdgarAnnualHistoryYear]:
    grouped: dict[int, list[EdgarFiling]] = {}
    for filing in filings:
        year = _filing_year(filing)
        if year is None:
            continue
        grouped.setdefault(year, []).append(filing)

    selected: list[EdgarAnnualHistoryYear] = []
    for year in sorted(grouped.keys(), reverse=True)[:years]:
        filings_for_year = grouped[year]
        ranked = sorted(
            filings_for_year,
            key=lambda filing: (
                filing.form in {"20-F", "40-F", "10-K"},
                filing.report_date or "",
                filing.filing_date,
                filing.accession_number,
            ),
            reverse=True,
        )
        selected.append(
            EdgarAnnualHistoryYear(
                year=year,
                selected_filing=ranked[0],
                alternate_filings=ranked[1:],
            )
        )
    return selected


def _missing_edgar_years(selected_years: list[EdgarAnnualHistoryYear], *, years: int) -> list[int]:
    if not selected_years:
        return []
    latest_year = max(item.year for item in selected_years)
    expected = {latest_year - offset for offset in range(years)}
    found = {item.year for item in selected_years}
    return sorted(expected - found, reverse=True)


def _filing_year(filing: EdgarFiling) -> int | None:
    for value in (filing.report_date, filing.filing_date):
        if not value:
            continue
        try:
            return date.fromisoformat(value).year
        except ValueError:
            continue
    return None


def _slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    return "-".join(part for part in cleaned.split("-") if part)
