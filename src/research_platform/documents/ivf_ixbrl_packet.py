from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

from research_platform.documents.ixbrl_summary import (
    IXBRLFactSet,
    IXBRLNarrativeFact,
    IXBRLNumericFact,
)

if TYPE_CHECKING:
    from research_platform.sources.market import FinancialHistory, MarketSnapshot

_STALENESS_THRESHOLD_MONTHS = 9


class PacketNumericFact(BaseModel):
    concept: str
    value: Optional[float] = None
    raw_text: Optional[str] = None
    unit: Optional[str] = None
    period: dict[str, str] = Field(default_factory=dict)
    dimensions: dict[str, str] = Field(default_factory=dict)


class PacketNarrativeFact(BaseModel):
    concept: str
    period: dict[str, str] = Field(default_factory=dict)
    text: str
    text_length: int


class IVFFIXBRLPacket(BaseModel):
    packet_type: str = "IVF_PRE_SCREEN_IXBRL_V1"
    report_metadata: dict[str, object]
    recency: dict[str, object]
    market_data: Optional[dict[str, object]] = None
    annual_narrative: Optional[str] = None
    numeric_facts: list[PacketNumericFact]
    narrative_facts: list[PacketNarrativeFact]
    post_period_narrative: Optional[str] = None
    evidence_gaps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class IVFFIXBRLPacketBuilder:
    def build(
        self,
        fact_set: IXBRLFactSet,
        post_period_fact_set: IXBRLFactSet | None = None,
        post_period_type: str = "INTERIM_OR_UPDATE",
        post_period_narrative: str | None = None,
        market_snapshot: MarketSnapshot | None = None,
        market_history: FinancialHistory | None = None,
        company_name: str | None = None,
        ticker: str | None = None,
        isin: str | None = None,
        annual_narrative: str | None = None,
    ) -> IVFFIXBRLPacket:
        numeric_facts = self._merge_numeric(fact_set, post_period_fact_set)
        narrative_facts = self._merge_narrative(fact_set, post_period_fact_set)
        recency = self._build_recency(
            fact_set, post_period_fact_set, post_period_type, post_period_narrative
        )
        evidence_gaps = self._build_evidence_gaps(fact_set, recency, annual_narrative)
        market_data = self._build_market_data(market_snapshot, market_history)

        report_metadata: dict[str, object] = {
            "company_name": company_name,
            "ticker": ticker,
            "isin": isin,
            "file_path": fact_set.file_path,
            "entity": fact_set.entity,
            "latest_duration_end_date": fact_set.latest_duration_end_date,
            "latest_instant_date": fact_set.latest_instant_date,
            "numeric_fact_count": len(numeric_facts),
            "narrative_fact_count": len(narrative_facts),
        }

        notes = [
            "Numeric facts are sorted latest period first. Narratives are sorted longest text first.",
            "Annual and post-period iXBRL facts are merged; periods identify the source.",
        ]

        return IVFFIXBRLPacket(
            report_metadata=report_metadata,
            recency=recency,
            market_data=market_data,
            annual_narrative=annual_narrative,
            numeric_facts=numeric_facts,
            narrative_facts=narrative_facts,
            post_period_narrative=post_period_narrative,
            evidence_gaps=evidence_gaps,
            notes=notes,
        )

    # ---------------------------------------------------------------------------
    # Merging
    # ---------------------------------------------------------------------------

    def _merge_numeric(
        self,
        annual: IXBRLFactSet,
        post_period: IXBRLFactSet | None,
    ) -> list[PacketNumericFact]:
        seen: set[tuple] = set()
        collected: list[PacketNumericFact] = []

        all_facts: list[IXBRLNumericFact] = list(annual.numeric_facts)
        if post_period:
            all_facts = list(post_period.numeric_facts) + all_facts

        for fact in all_facts:
            key = (fact.concept, str(fact.period), str(fact.dimensions))
            if key in seen:
                continue
            seen.add(key)
            collected.append(self._to_packet_numeric(fact))

        return collected

    def _merge_narrative(
        self,
        annual: IXBRLFactSet,
        post_period: IXBRLFactSet | None,
    ) -> list[PacketNarrativeFact]:
        seen: set[tuple] = set()
        collected: list[PacketNarrativeFact] = []

        all_facts: list[IXBRLNarrativeFact] = []
        if post_period:
            all_facts += list(post_period.narrative_facts)
        all_facts += list(annual.narrative_facts)

        for fact in all_facts:
            key = (fact.concept, str(fact.period))
            if key in seen:
                continue
            seen.add(key)
            collected.append(self._to_packet_narrative(fact))

        return collected

    # ---------------------------------------------------------------------------
    # Recency
    # ---------------------------------------------------------------------------

    def _build_recency(
        self,
        annual: IXBRLFactSet,
        post_period_ixbrl: IXBRLFactSet | None,
        post_period_type: str,
        post_period_narrative: str | None,
    ) -> dict[str, object]:
        today = date.today()
        annual_end = annual.latest_duration_end_date
        annual_age_months: int | None = None

        if annual_end:
            try:
                d = date.fromisoformat(annual_end)
                annual_age_months = (today.year - d.year) * 12 + (today.month - d.month)
            except ValueError:
                pass

        # iXBRL post-period (structured facts available)
        if post_period_ixbrl:
            post_end = post_period_ixbrl.latest_duration_end_date or post_period_ixbrl.latest_instant_date
            post_age_months: int | None = None
            if post_end:
                try:
                    d = date.fromisoformat(post_end)
                    post_age_months = (today.year - d.year) * 12 + (today.month - d.month)
                except ValueError:
                    pass
            return {
                "annual_period_end": annual_end,
                "annual_age_months": annual_age_months,
                "post_period_update_available": True,
                "post_period_format": "IXBRL",
                "post_period_type": post_period_type,
                "post_period_end": post_end,
                "post_period_age_months": post_age_months,
                "is_stale": False,
            }

        # Narrative post-period (PDF/HTML text, no structured facts)
        if post_period_narrative:
            return {
                "annual_period_end": annual_end,
                "annual_age_months": annual_age_months,
                "post_period_update_available": True,
                "post_period_format": "NARRATIVE",
                "post_period_type": post_period_type,
                "post_period_end": None,
                "post_period_age_months": None,
                "is_stale": False,
            }

        stale = (
            annual_age_months is not None
            and annual_age_months >= _STALENESS_THRESHOLD_MONTHS
        )
        return {
            "annual_period_end": annual_end,
            "annual_age_months": annual_age_months,
            "post_period_update_available": False,
            "post_period_format": None,
            "post_period_type": None,
            "post_period_end": None,
            "post_period_age_months": None,
            "is_stale": stale,
        }

    # ---------------------------------------------------------------------------
    # Market data
    # ---------------------------------------------------------------------------

    @staticmethod
    def _build_market_data(
        snapshot: MarketSnapshot | None,
        history: FinancialHistory | None,
    ) -> dict[str, object] | None:
        if snapshot is None:
            return None
        result: dict[str, object] = {
            "snapshot": snapshot.model_dump(mode="json"),
        }
        if history is not None:
            result["history"] = history.model_dump(mode="json")
        return result

    # ---------------------------------------------------------------------------
    # Evidence gaps
    # ---------------------------------------------------------------------------

    def _build_evidence_gaps(
        self,
        fact_set: IXBRLFactSet,
        recency: dict[str, object],
        annual_narrative: str | None = None,
    ) -> list[str]:
        gaps: list[str] = []

        if not fact_set.numeric_facts:
            if annual_narrative:
                gaps.append(
                    "Annual report is PDF/HTML only — structured iXBRL facts not available. "
                    "Narrative text and market data are the primary sources."
                )
            else:
                gaps.append("No numeric iXBRL facts were extracted from the filing.")

        if not fact_set.narrative_facts and not annual_narrative:
            gaps.append("No narrative iXBRL facts were extracted from the filing.")

        if fact_set.narrative_facts:
            has_going_concern = any(
                "goingconcern" in f.concept.lower().replace(":", "").replace("-", "").replace("_", "")
                for f in fact_set.narrative_facts
            )
            if not has_going_concern:
                gaps.append("No tagged going concern narrative found in the filing.")

        if recency.get("is_stale"):
            age = recency.get("annual_age_months")
            end = recency.get("annual_period_end") or "unknown date"
            gaps.append(
                f"Annual report is {age} months old (period ended {end}) and no subsequent "
                f"interim report or trading update has been supplied. Developments since "
                f"{end} are not reflected in this packet."
            )

        return gaps

    # ---------------------------------------------------------------------------
    # Converters
    # ---------------------------------------------------------------------------

    @staticmethod
    def _to_packet_numeric(fact: IXBRLNumericFact) -> PacketNumericFact:
        return PacketNumericFact(
            concept=fact.concept,
            value=fact.value,
            raw_text=fact.raw_text,
            unit=fact.unit,
            period=fact.period,
            dimensions=fact.dimensions,
        )

    @staticmethod
    def _to_packet_narrative(fact: IXBRLNarrativeFact) -> PacketNarrativeFact:
        return PacketNarrativeFact(
            concept=fact.concept,
            period=fact.period,
            text=fact.text,
            text_length=len(fact.text),
        )
