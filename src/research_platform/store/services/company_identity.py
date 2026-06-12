"""Services for persisting company identity into the store."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from research_platform.sources.gleif import GLEIFRecord
from research_platform.store.models import Company, Identifier
from research_platform.store.repositories.company_identity import CompanyIdentityRepository


@dataclass(slots=True)
class CompanyIdentityWriteResult:
    """Summary of a company identity upsert."""

    company_id: str
    created_company: bool


class CompanyIdentityService:
    """Upsert company identity records derived from external sources."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CompanyIdentityRepository(session)

    def upsert_from_gleif(self, *, record: GLEIFRecord) -> CompanyIdentityWriteResult:
        company = self.repo.get_company_by_identifier(id_type="LEI", id_value=record.lei)

        created_company = company is None
        if company is None:
            company = self.repo.add_company(
                Company(
                    name=record.legal_name,
                    legal_name=record.legal_name,
                    country=record.country,
                )
            )
        else:
            company.name = record.legal_name
            company.legal_name = record.legal_name
            company.country = record.country

        self._ensure_identifier(
            company_id=company.company_id,
            id_type="LEI",
            id_value=record.lei,
            source="GLEIF",
            is_primary=True,
        )
        if record.registered_as:
            self._ensure_identifier(
                company_id=company.company_id,
                id_type="REGISTERED_AS",
                id_value=record.registered_as,
                source="GLEIF",
            )
        for isin in record.isins:
            self._ensure_identifier(
                company_id=company.company_id,
                id_type="ISIN",
                id_value=isin,
                source="GLEIF",
            )

        return CompanyIdentityWriteResult(
            company_id=str(company.company_id),
            created_company=created_company,
        )

    def _ensure_identifier(
        self,
        *,
        company_id,
        id_type: str,
        id_value: str,
        source: str,
        is_primary: bool = False,
    ) -> Identifier:
        identifier = self.repo.get_identifier(
            company_id=company_id,
            id_type=id_type,
            id_value=id_value,
        )
        if identifier is None:
            identifier = self.repo.add_identifier(
                Identifier(
                    company_id=company_id,
                    id_type=id_type,
                    id_value=id_value,
                    source=source,
                    is_primary=is_primary,
                )
            )
        else:
            identifier.source = source
            if is_primary:
                identifier.is_primary = True
        return identifier
