from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from research_platform.core.logging import get_logger

logger = get_logger(__name__)


class GLEIFError(RuntimeError):
    """Raised when a GLEIF lookup fails or returns no usable entity."""


@dataclass(slots=True)
class GLEIFRecord:
    lei: str
    legal_name: str
    status: str | None = None
    registration_status: str | None = None
    jurisdiction: str | None = None
    country: str | None = None
    city: str | None = None
    registered_as: str | None = None
    other_names: list[str] = field(default_factory=list)
    isins: list[str] = field(default_factory=list)


class GLEIFClient:
    def __init__(self, base_url: str = "https://api.gleif.org/api/v1") -> None:
        self.base_url = base_url.rstrip("/")

    def search_company(
        self,
        name: str,
        *,
        country: str | None = None,
        limit: int = 10,
    ) -> list[GLEIFRecord]:
        """Search GLEIF LEI records by legal name, falling back to fuzzy completion."""
        records = self._search_lei_records(name=name, country=country, limit=limit)
        if records:
            return records

        record_ids = self._fuzzy_completion_ids(name, limit=limit)
        resolved: list[GLEIFRecord] = []
        for lei in record_ids:
            record = self.get_record(lei)
            if country and (record.country or "").upper() != country.upper():
                continue
            resolved.append(record)
        if not resolved:
            raise GLEIFError(f"No usable LEI record found for company name {name!r}")
        return resolved[:limit]

    def resolve_company(
        self,
        name: str,
        *,
        country: str | None = None,
        limit: int = 10,
    ) -> GLEIFRecord:
        records = self.search_company(name, country=country, limit=limit)
        return max(records, key=lambda record: _record_score(query=name, record=record, country=country))

    def get_record(self, lei: str) -> GLEIFRecord:
        payload = self._get_json(f"/lei-records/{lei}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise GLEIFError(f"No LEI record found for {lei!r}")
        return _to_record(data)

    def get_isins(self, lei: str) -> list[str]:
        payload = self._get_json(f"/lei-records/{lei}/isins")
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        isins = [
            item.get("attributes", {}).get("isin")
            for item in data
            if isinstance(item, dict)
        ]
        return [isin for isin in isins if isinstance(isin, str) and isin]

    def _search_lei_records(self, *, name: str, country: str | None, limit: int) -> list[GLEIFRecord]:
        params: dict[str, Any] = {
            "filter[entity.legalName]": name,
            "page[size]": limit,
        }
        if country:
            params["filter[entity.legalAddress.country]"] = country.upper()

        payload = self._get_json("/lei-records", params=params)
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [_to_record(item) for item in data if isinstance(item, dict)]

    def _fuzzy_completion_ids(self, query: str, *, limit: int) -> list[str]:
        payload = self._get_json(
            "/fuzzycompletions",
            params={"field": "entity.legalName", "q": query},
        )
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        ids: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            relationships = item.get("relationships", {})
            lei_records = relationships.get("lei-records", {})
            data_obj = lei_records.get("data")
            if isinstance(data_obj, dict):
                lei = data_obj.get("id")
                if isinstance(lei, str) and lei:
                    ids.append(lei)
            if len(ids) >= limit:
                break
        return ids

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"{self.base_url}{path}",
                params=params,
                timeout=20,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GLEIFError(f"GLEIF request failed for {path}: {exc}") from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise GLEIFError(f"Unexpected GLEIF response shape for {path}")
        return payload


def _to_record(item: dict[str, Any]) -> GLEIFRecord:
    attributes = item.get("attributes", {})
    entity = attributes.get("entity", {})
    registration = attributes.get("registration", {})
    legal_name = entity.get("legalName", {})
    legal_address = entity.get("legalAddress", {})
    other_names = entity.get("otherNames", [])

    return GLEIFRecord(
        lei=str(attributes.get("lei") or item.get("id") or ""),
        legal_name=str(legal_name.get("name") or ""),
        status=_optional_str(entity.get("status")),
        registration_status=_optional_str(registration.get("status")),
        jurisdiction=_optional_str(entity.get("jurisdiction")),
        country=_optional_str(legal_address.get("country")),
        city=_optional_str(legal_address.get("city")),
        registered_as=_optional_str(entity.get("registeredAs")),
        other_names=[
            name_item.get("name")
            for name_item in other_names
            if isinstance(name_item, dict) and isinstance(name_item.get("name"), str)
        ],
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _record_score(*, query: str, record: GLEIFRecord, country: str | None) -> tuple[int, int, int, int]:
    normalized_query = _normalize_text(query)
    normalized_name = _normalize_text(record.legal_name)
    query_tokens = set(normalized_query.split())
    name_tokens = set(normalized_name.split())

    if normalized_query == normalized_name:
        name_score = 4
    elif normalized_query and normalized_query in normalized_name:
        name_score = 3
    elif query_tokens and query_tokens.issubset(name_tokens):
        name_score = 2
    else:
        name_score = 1 if query_tokens.intersection(name_tokens) else 0

    country_score = 1 if country and (record.country or "").upper() == country.upper() else 0
    entity_status_score = 1 if (record.status or "").upper() == "ACTIVE" else 0
    registration_status_score = 1 if (record.registration_status or "").upper() == "ISSUED" else 0
    return (name_score, country_score, entity_status_score, registration_status_score)


def _normalize_text(value: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else " " for character in value)
    stop_words = {"the", "plc", "limited", "ltd", "group", "holdings", "corp", "inc", "company"}
    tokens = [token for token in cleaned.split() if token and token not in stop_words]
    return " ".join(tokens)
