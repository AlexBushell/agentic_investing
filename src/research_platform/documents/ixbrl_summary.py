from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from research_platform.documents.ixbrl_extractor import (
    IXBRLContext,
    IXBRLExtractionResult,
    IXBRLFact,
)


class IXBRLKeyMetric(BaseModel):
    name: str
    concept: str
    value: Optional[float] = None
    raw_text: Optional[str] = None
    unit: Optional[str] = None
    period: dict[str, str] = Field(default_factory=dict)
    dimensions: dict[str, str] = Field(default_factory=dict)
    context_ref: Optional[str] = None


class IXBRLNarrativeHighlight(BaseModel):
    concept: str
    period: dict[str, str] = Field(default_factory=dict)
    preview: str
    text_length: int


class IXBRLSummaryResult(BaseModel):
    file_path: str
    entity: Optional[str] = None
    latest_duration_end_date: Optional[str] = None
    latest_instant_date: Optional[str] = None
    key_metrics: list[IXBRLKeyMetric] = Field(default_factory=list)
    narrative_highlights: list[IXBRLNarrativeHighlight] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class IXBRLSummarizer:
    DURATION_METRICS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
        ("revenue", ("ifrs-full:Revenue", "tescoplc:RevenueFromSaleOfGoodsAndServices"), ()),
        ("gross_profit", ("ifrs-full:GrossProfit",), ()),
        ("operating_profit", ("ifrs-full:ProfitLossFromOperatingActivities",), ()),
        (
            "adjusted_operating_profit",
            ("ifrs-full:ProfitLossFromOperatingActivities",),
            ("tescoplc:BeforeAdjustingItemsMember",),
        ),
        ("profit_before_tax", ("ifrs-full:ProfitLossBeforeTax",), ()),
        (
            "adjusted_profit_before_tax",
            ("ifrs-full:ProfitLossBeforeTax",),
            ("tescoplc:BeforeAdjustingItemsMember",),
        ),
        (
            "basic_eps",
            (
                "ifrs-full:BasicEarningsLossPerShare",
                "ifrs-full:BasicEarningsLossPerShareFromContinuingOperations",
            ),
            (),
        ),
        (
            "diluted_eps",
            (
                "ifrs-full:DilutedEarningsLossPerShare",
                "ifrs-full:DilutedEarningsLossPerShareFromContinuingOperations",
            ),
            (),
        ),
        (
            "dividend_per_share",
            (
                "ifrs-full:DividendsProposedOrDeclaredPerShare",
                "tescoplc:DividendPerOrdinaryShare",
            ),
            (),
        ),
    ]

    INSTANT_METRICS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
        (
            "cash_and_cash_equivalents",
            ("ifrs-full:CashAndCashEquivalents",),
            (),
        ),
        ("borrowings", ("ifrs-full:Borrowings",), ()),
        ("net_debt", ("tescoplc:NetDebt",), ()),
        (
            "lease_liabilities",
            ("ifrs-full:LeaseLiabilities",),
            (),
        ),
        (
            "total_equity",
            ("ifrs-full:Equity",),
            (),
        ),
    ]

    NARRATIVE_KEYWORDS: tuple[str, ...] = (
        "goingconcern",
        "liquidityrisk",
        "financialriskmanagement",
        "principalrisks",
        "viability",
        "borrowing",
        "capitalmanagement",
    )

    def summarize(self, extraction: IXBRLExtractionResult) -> IXBRLSummaryResult:
        duration_end = self._latest_duration_end_date(extraction.facts)
        instant_date = self._latest_instant_date(extraction.facts)
        entity = self._first_entity(extraction.facts)

        key_metrics: list[IXBRLKeyMetric] = []
        for name, concepts, dimension_preferences in self.DURATION_METRICS:
            fact = self._pick_numeric_fact(
                extraction.facts,
                concepts=concepts,
                period_type="duration",
                latest_date=duration_end,
                preferred_dimension_members=dimension_preferences,
            )
            if fact is not None:
                key_metrics.append(self._to_key_metric(name, fact))

        for name, concepts, dimension_preferences in self.INSTANT_METRICS:
            fact = self._pick_numeric_fact(
                extraction.facts,
                concepts=concepts,
                period_type="instant",
                latest_date=instant_date,
                preferred_dimension_members=dimension_preferences,
            )
            if fact is not None:
                key_metrics.append(self._to_key_metric(name, fact))

        narrative_highlights = self._pick_narrative_highlights(extraction.facts)
        notes = [
            "Summary metrics prefer the latest reported period and favor dimensionless facts unless a dimension-specific metric is explicitly requested.",
            "Narrative highlights are selected from tagged iXBRL non-numeric disclosures using keyword matching.",
        ]

        return IXBRLSummaryResult(
            file_path=extraction.file_path,
            entity=entity,
            latest_duration_end_date=duration_end.isoformat() if duration_end else None,
            latest_instant_date=instant_date.isoformat() if instant_date else None,
            key_metrics=key_metrics,
            narrative_highlights=narrative_highlights,
            notes=notes,
        )

    def _pick_numeric_fact(
        self,
        facts: list[IXBRLFact],
        concepts: tuple[str, ...],
        period_type: str,
        latest_date: Optional[date],
        preferred_dimension_members: tuple[str, ...],
    ) -> Optional[IXBRLFact]:
        candidates: list[IXBRLFact] = []
        for fact in facts:
            if fact.fact_type != "numeric" or fact.value is None or fact.concept not in concepts:
                continue
            context = fact.context
            if context is None:
                continue
            if self._context_period_type(context) != period_type:
                continue
            if latest_date and self._context_relevant_date(context, period_type) != latest_date:
                continue
            candidates.append(fact)

        if not candidates:
            return None

        return max(
            candidates,
            key=lambda fact: self._numeric_fact_score(fact, preferred_dimension_members),
        )

    def _pick_narrative_highlights(
        self,
        facts: list[IXBRLFact],
    ) -> list[IXBRLNarrativeHighlight]:
        matches: list[IXBRLNarrativeHighlight] = []
        seen_concepts: set[str] = set()

        for fact in facts:
            if fact.fact_type != "narrative" or not fact.concept or not fact.text:
                continue
            normalized_concept = fact.concept.lower().replace(":", "")
            if not any(keyword in normalized_concept for keyword in self.NARRATIVE_KEYWORDS):
                continue
            if fact.concept in seen_concepts:
                continue

            seen_concepts.add(fact.concept)
            preview = fact.text[:500].strip()
            matches.append(
                IXBRLNarrativeHighlight(
                    concept=fact.concept,
                    period=fact.context.period if fact.context else {},
                    preview=preview,
                    text_length=len(fact.text),
                )
            )

        matches.sort(key=lambda item: (item.text_length, item.concept), reverse=True)
        return matches[:5]

    def _numeric_fact_score(
        self,
        fact: IXBRLFact,
        preferred_dimension_members: tuple[str, ...],
    ) -> tuple[int, int, int]:
        context = fact.context
        dimensions = context.dimensions if context else {}
        dimension_values = tuple(dimensions.values())

        preferred_match = 0
        if preferred_dimension_members:
            if any(member in dimension_values for member in preferred_dimension_members):
                preferred_match = 3
            elif not dimensions:
                preferred_match = 1
        else:
            preferred_match = 2 if not dimensions else 0

        concept_specificity = 1 if fact.concept and fact.concept.startswith("tescoplc:") else 0
        dimension_penalty = -len(dimensions)
        return (preferred_match, concept_specificity, dimension_penalty)

    def _to_key_metric(self, name: str, fact: IXBRLFact) -> IXBRLKeyMetric:
        context = fact.context or IXBRLContext(id="", period={}, dimensions={})
        return IXBRLKeyMetric(
            name=name,
            concept=fact.concept or "",
            value=fact.value,
            raw_text=fact.raw_text,
            unit=fact.unit,
            period=context.period,
            dimensions=context.dimensions,
            context_ref=fact.context_ref,
        )

    def _latest_duration_end_date(self, facts: list[IXBRLFact]) -> Optional[date]:
        dates = [
            relevant
            for fact in facts
            if fact.context is not None
            and self._context_period_type(fact.context) == "duration"
            and (relevant := self._context_relevant_date(fact.context, "duration")) is not None
        ]
        return max(dates) if dates else None

    def _latest_instant_date(self, facts: list[IXBRLFact]) -> Optional[date]:
        dates = [
            relevant
            for fact in facts
            if fact.context is not None
            and self._context_period_type(fact.context) == "instant"
            and (relevant := self._context_relevant_date(fact.context, "instant")) is not None
        ]
        return max(dates) if dates else None

    def _first_entity(self, facts: list[IXBRLFact]) -> Optional[str]:
        for fact in facts:
            if fact.context and fact.context.entity:
                return fact.context.entity
        return None

    @staticmethod
    def _context_period_type(context: IXBRLContext) -> Optional[str]:
        period = context.period
        if "instant" in period:
            return "instant"
        if "startDate" in period and "endDate" in period:
            return "duration"
        return None

    @staticmethod
    def _context_relevant_date(context: IXBRLContext, period_type: str) -> Optional[date]:
        try:
            if period_type == "instant":
                instant = context.period.get("instant")
                return date.fromisoformat(instant) if instant else None
            end_date = context.period.get("endDate")
            return date.fromisoformat(end_date) if end_date else None
        except ValueError:
            return None
