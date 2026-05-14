from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from research_platform.documents.ixbrl_summary import (
    IXBRLFactSet,
    IXBRLNarrativeFact,
    IXBRLNumericFact,
)
from research_platform.routing.issuer_router import IssuerRoutingProfile


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
    issuer_routing_profile: IssuerRoutingProfile
    report_metadata: dict[str, object]
    numeric_facts: list[PacketNumericFact]
    narrative_facts: list[PacketNarrativeFact]
    evidence_gaps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class IVFFIXBRLPacketBuilder:
    def build(
        self,
        fact_set: IXBRLFactSet,
        routing_profile: IssuerRoutingProfile,
    ) -> IVFFIXBRLPacket:
        numeric_facts = [self._to_packet_numeric(f) for f in fact_set.numeric_facts]
        narrative_facts = [self._to_packet_narrative(f) for f in fact_set.narrative_facts]
        evidence_gaps = self._build_evidence_gaps(fact_set, routing_profile)

        report_metadata: dict[str, object] = {
            "file_path": fact_set.file_path,
            "entity": fact_set.entity,
            "latest_duration_end_date": fact_set.latest_duration_end_date,
            "latest_instant_date": fact_set.latest_instant_date,
            "numeric_fact_count": len(numeric_facts),
            "narrative_fact_count": len(narrative_facts),
        }

        notes = [
            "Packet contains all deduped iXBRL facts; no concept filtering has been applied.",
            "Numeric facts are sorted latest period first. Narratives are sorted longest text first.",
        ]

        return IVFFIXBRLPacket(
            issuer_routing_profile=routing_profile,
            report_metadata=report_metadata,
            numeric_facts=numeric_facts,
            narrative_facts=narrative_facts,
            evidence_gaps=evidence_gaps,
            notes=notes,
        )

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

    def _build_evidence_gaps(
        self,
        fact_set: IXBRLFactSet,
        routing_profile: IssuerRoutingProfile,
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

        has_revenue = any(
            any(
                term in f.concept.lower().replace(":", "").replace("-", "").replace("_", "")
                for term in ("revenue", "grossprofit", "turnover")
            )
            for f in fact_set.numeric_facts
        )
        if not has_revenue:
            gaps.append("No revenue or turnover concept found in numeric facts.")

        if routing_profile.ivf_eligibility != "IVF_ELIGIBLE":
            gaps.append(
                f"Issuer is not currently IVF-eligible: {routing_profile.issuer_archetype}"
            )

        return gaps
