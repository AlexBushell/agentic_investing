from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from research_platform.documents.ixbrl_summary import (
    IXBRLFactSet,
    IXBRLNarrativeFact,
    IXBRLNumericFact,
)

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
    numeric_facts: list[PacketNumericFact]
    narrative_facts: list[PacketNarrativeFact]
    evidence_gaps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class IVFFIXBRLPacketBuilder:
    def build(
        self,
        fact_set: IXBRLFactSet,
        post_period_fact_set: IXBRLFactSet | None = None,
        post_period_type: str = "INTERIM_OR_UPDATE",
    ) -> IVFFIXBRLPacket:
        numeric_facts = self._merge_numeric(fact_set, post_period_fact_set)
        narrative_facts = self._merge_narrative(fact_set, post_period_fact_set)
        recency = self._build_recency(fact_set, post_period_fact_set, post_period_type)
        evidence_gaps = self._build_evidence_gaps(fact_set, recency)

        report_metadata: dict[str, object] = {
            "file_path": fact_set.file_path,
            "entity": fact_set.entity,
            "latest_duration_end_date": fact_set.latest_duration_end_date,
            "latest_instant_date": fact_set.latest_instant_date,
            "numeric_fact_count": len(numeric_facts),
            "narrative_fact_count": len(narrative_facts),
        }

        notes = [
            "Numeric facts are sorted latest period first. Narratives are sorted longest text first.",
            "Annual and post-period facts are merged; periods identify the source.",
        ]

        return IVFFIXBRLPacket(
            report_metadata=report_metadata,
            recency=recency,
            numeric_facts=numeric_facts,
            narrative_facts=narrative_facts,
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
        post_period: IXBRLFactSet | None,
        post_period_type: str,
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

        if post_period:
            post_end = post_period.latest_duration_end_date or post_period.latest_instant_date
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
                "post_period_type": post_period_type,
                "post_period_end": post_end,
                "post_period_age_months": post_age_months,
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
            "post_period_type": None,
            "post_period_end": None,
            "post_period_age_months": None,
            "is_stale": stale,
        }

    # ---------------------------------------------------------------------------
    # Evidence gaps
    # ---------------------------------------------------------------------------

    def _build_evidence_gaps(
        self,
        fact_set: IXBRLFactSet,
        recency: dict[str, object],
    ) -> list[str]:
        gaps: list[str] = []

        if not fact_set.numeric_facts:
            gaps.append("No numeric iXBRL facts were extracted from the filing.")

        if not fact_set.narrative_facts:
            gaps.append("No narrative iXBRL facts were extracted from the filing.")

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
