"""Persistence helpers for company identity data."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from research_platform.store.models import Company, Identifier, Listing


class CompanyIdentityRepository:
    """Repository for canonical company identity persistence."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_company_by_identifier(self, *, id_type: str, id_value: str) -> Company | None:
        stmt = (
            select(Company)
            .join(Identifier, Identifier.company_id == Company.company_id)
            .where(Identifier.id_type == id_type, Identifier.id_value == id_value)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_identifier(self, *, company_id, id_type: str, id_value: str) -> Identifier | None:
        stmt = select(Identifier).where(
            Identifier.company_id == company_id,
            Identifier.id_type == id_type,
            Identifier.id_value == id_value,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_listing(self, *, company_id, ticker: str, exchange_code: str | None) -> Listing | None:
        stmt = select(Listing).where(
            Listing.company_id == company_id,
            Listing.ticker == ticker,
            Listing.exchange_code == exchange_code,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def add_company(self, company: Company) -> Company:
        self.session.add(company)
        self.session.flush()
        return company

    def add_identifier(self, identifier: Identifier) -> Identifier:
        self.session.add(identifier)
        self.session.flush()
        return identifier

    def add_listing(self, listing: Listing) -> Listing:
        self.session.add(listing)
        self.session.flush()
        return listing

