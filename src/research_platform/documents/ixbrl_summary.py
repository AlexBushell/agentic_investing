from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from research_platform.documents.ixbrl_extractor import (
    IXBRLExtractionResult,
    IXBRLFact,
)


class IXBRLNumericFact(BaseModel):
    concept: str
    value: Optional[float] = None
    raw_text: Optional[str] = None
    unit: Optional[str] = None
    period: dict[str, str] = Field(default_factory=dict)
    dimensions: dict[str, str] = Field(default_factory=dict)


class IXBRLNarrativeFact(BaseModel):
    concept: str
    period: dict[str, str] = Field(default_factory=dict)
    text: str


class IXBRLFactSet(BaseModel):
    file_path: str
    entity: Optional[str] = None
    latest_duration_end_date: Optional[str] = None
    latest_instant_date: Optional[str] = None
    numeric_facts: list[IXBRLNumericFact] = Field(default_factory=list)
    narrative_facts: list[IXBRLNarrativeFact] = Field(default_factory=list)


class IXBRLFactSetBuilder:
    def build(self, extraction: IXBRLExtractionResult) -> IXBRLFactSet:
        entity = self._first_entity(extraction.facts)
        latest_duration_end = self._latest_duration_end_date(extraction.facts)
        latest_instant = self._latest_instant_date(extraction.facts)
        numeric_facts = self._collect_numeric(extraction.facts)
        narrative_facts = self._collect_narratives(extraction.facts)

        return IXBRLFactSet(
            file_path=extraction.file_path,
            entity=entity,
            latest_duration_end_date=latest_duration_end.isoformat() if latest_duration_end else None,
            latest_instant_date=latest_instant.isoformat() if latest_instant else None,
            numeric_facts=numeric_facts,
            narrative_facts=narrative_facts,
        )

    def _collect_numeric(self, facts: list[IXBRLFact]) -> list[IXBRLNumericFact]:
        seen: set[tuple] = set()
        collected: list[IXBRLNumericFact] = []

        for fact in facts:
            if fact.fact_type != "numeric" or not fact.concept or fact.value is None:
                continue
            period = fact.context.period if fact.context else {}
            dimensions = fact.context.dimensions if fact.context else {}
            key = (fact.concept, str(period), str(dimensions))
            if key in seen:
                continue
            seen.add(key)
            collected.append(IXBRLNumericFact(
                concept=fact.concept,
                value=fact.value,
                raw_text=fact.raw_text,
                unit=fact.unit,
                period=period,
                dimensions=dimensions,
            ))

        collected.sort(
            key=lambda f: (
                f.period.get("endDate") or f.period.get("instant") or "",
                f.concept,
            ),
            reverse=True,
        )
        return collected

    def _collect_narratives(self, facts: list[IXBRLFact]) -> list[IXBRLNarrativeFact]:
        best: dict[tuple, IXBRLNarrativeFact] = {}

        for fact in facts:
            if fact.fact_type != "narrative" or not fact.concept or not fact.text:
                continue
            period = fact.context.period if fact.context else {}
            key = (fact.concept, str(period))
            existing = best.get(key)
            if existing is None or len(fact.text) > len(existing.text):
                best[key] = IXBRLNarrativeFact(
                    concept=fact.concept,
                    period=period,
                    text=fact.text,
                )

        return sorted(best.values(), key=lambda f: len(f.text), reverse=True)

    def _first_entity(self, facts: list[IXBRLFact]) -> Optional[str]:
        for fact in facts:
            if fact.context and fact.context.entity:
                return fact.context.entity
        return None

    def _latest_duration_end_date(self, facts: list[IXBRLFact]) -> Optional[date]:
        dates = []
        for fact in facts:
            if fact.context is None:
                continue
            period = fact.context.period
            if "startDate" in period and "endDate" in period:
                try:
                    dates.append(date.fromisoformat(period["endDate"]))
                except ValueError:
                    pass
        return max(dates) if dates else None

    def _latest_instant_date(self, facts: list[IXBRLFact]) -> Optional[date]:
        dates = []
        for fact in facts:
            if fact.context is None:
                continue
            period = fact.context.period
            if "instant" in period:
                try:
                    dates.append(date.fromisoformat(period["instant"]))
                except ValueError:
                    pass
        return max(dates) if dates else None
