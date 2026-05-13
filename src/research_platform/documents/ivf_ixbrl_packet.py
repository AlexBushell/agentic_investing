from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from research_platform.documents.ixbrl_extractor import IXBRLExtractionResult, IXBRLFact
from research_platform.documents.ixbrl_summary import IXBRLSummaryResult
from research_platform.routing.issuer_router import IssuerRoutingProfile


class PacketFact(BaseModel):
    concept: str
    raw_text: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    period: dict[str, str] = Field(default_factory=dict)
    dimensions: dict[str, str] = Field(default_factory=dict)
    context_ref: Optional[str] = None


class PacketNarrative(BaseModel):
    concept: str
    period: dict[str, str] = Field(default_factory=dict)
    text: str
    text_length: int


class IVFFIXBRLPacket(BaseModel):
    packet_type: str = "IVF_PRE_SCREEN_IXBRL_BROAD_V0"
    issuer_routing_profile: IssuerRoutingProfile
    report_metadata: dict[str, object]
    ixbrl_summary: dict[str, object]
    key_numeric_facts: dict[str, list[PacketFact]]
    key_narrative_facts: dict[str, list[PacketNarrative]]
    evidence_gaps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class IVFFIXBRLPacketBuilder:
    NUMERIC_BUCKETS: dict[str, tuple[str, ...]] = {
        "income_statement_facts": (
            "revenue",
            "sales",
            "grossprofit",
            "profitlossfromoperatingactivities",
            "profitlossbeforetax",
            "earningspershare",
            "dividend",
        ),
        "debt_liquidity_facts": (
            "borrowings",
            "debt",
            "cashandcashequivalents",
            "lease",
            "liabilities",
            "netdebt",
            "financecosts",
        ),
        "balance_sheet_facts": (
            "equity",
            "assets",
            "liabilities",
            "goodwill",
            "propertyplantandequipment",
            "intang",
        ),
    }

    NARRATIVE_BUCKETS: dict[str, tuple[str, ...]] = {
        "going_concern_and_audit_evidence": (
            "goingconcern",
            "audit",
            "materialuncertainty",
        ),
        "debt_and_liquidity_evidence": (
            "liquidityrisk",
            "borrowing",
            "financialriskmanagement",
            "capitalriskmanagement",
            "capitalmanagement",
        ),
        "risk_and_viability_evidence": (
            "principalrisks",
            "viability",
            "riskmanagement",
        ),
    }

    def build(
        self,
        summary: IXBRLSummaryResult,
        extraction: IXBRLExtractionResult,
        routing_profile: IssuerRoutingProfile,
    ) -> IVFFIXBRLPacket:
        key_numeric_facts = {
            bucket: self._collect_numeric_bucket(extraction.facts, keywords)
            for bucket, keywords in self.NUMERIC_BUCKETS.items()
        }
        key_narrative_facts = {
            bucket: self._collect_narrative_bucket(extraction.facts, keywords)
            for bucket, keywords in self.NARRATIVE_BUCKETS.items()
        }

        evidence_gaps = self._build_evidence_gaps(
            summary=summary,
            routing_profile=routing_profile,
            key_numeric_facts=key_numeric_facts,
            key_narrative_facts=key_narrative_facts,
        )

        report_metadata = {
            "file_path": summary.file_path,
            "entity": summary.entity,
            "latest_duration_end_date": summary.latest_duration_end_date,
            "latest_instant_date": summary.latest_instant_date,
            "context_count": extraction.context_count,
            "numeric_fact_count": extraction.numeric_fact_count,
            "narrative_fact_count": extraction.narrative_fact_count,
        }

        notes = [
            "This is a broad first-pass IVF packet built from iXBRL extraction and routing output.",
            "The packet intentionally includes more facts than a final production packet so we can observe what the LLM actually uses.",
        ]

        return IVFFIXBRLPacket(
            issuer_routing_profile=routing_profile,
            report_metadata=report_metadata,
            ixbrl_summary=summary.model_dump(mode="json"),
            key_numeric_facts=key_numeric_facts,
            key_narrative_facts=key_narrative_facts,
            evidence_gaps=evidence_gaps,
            notes=notes,
        )

    def _collect_numeric_bucket(
        self,
        facts: list[IXBRLFact],
        keywords: tuple[str, ...],
    ) -> list[PacketFact]:
        collected: list[PacketFact] = []
        seen: set[tuple[str, str, str]] = set()

        for fact in facts:
            if fact.fact_type != "numeric" or not fact.concept:
                continue
            concept_key = fact.concept.lower().replace(":", "")
            if not any(keyword in concept_key for keyword in keywords):
                continue
            period = fact.context.period if fact.context else {}
            dimensions = fact.context.dimensions if fact.context else {}
            dedupe_key = (
                fact.concept,
                str(period),
                str(dimensions),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            collected.append(
                PacketFact(
                    concept=fact.concept,
                    raw_text=fact.raw_text,
                    value=fact.value,
                    unit=fact.unit,
                    period=period,
                    dimensions=dimensions,
                    context_ref=fact.context_ref,
                )
            )

        collected.sort(
            key=lambda fact: (
                fact.period.get("endDate") or fact.period.get("instant") or "",
                -len(fact.dimensions),
                fact.concept,
            ),
            reverse=True,
        )
        return collected[:30]

    def _collect_narrative_bucket(
        self,
        facts: list[IXBRLFact],
        keywords: tuple[str, ...],
    ) -> list[PacketNarrative]:
        collected: list[PacketNarrative] = []
        seen_concepts: set[str] = set()

        for fact in facts:
            if fact.fact_type != "narrative" or not fact.concept or not fact.text:
                continue
            concept_key = fact.concept.lower().replace(":", "")
            if not any(keyword in concept_key for keyword in keywords):
                continue
            if fact.concept in seen_concepts:
                continue
            seen_concepts.add(fact.concept)
            collected.append(
                PacketNarrative(
                    concept=fact.concept,
                    period=fact.context.period if fact.context else {},
                    text=fact.text,
                    text_length=len(fact.text),
                )
            )

        collected.sort(key=lambda item: (item.text_length, item.concept), reverse=True)
        return collected[:10]

    def _build_evidence_gaps(
        self,
        summary: IXBRLSummaryResult,
        routing_profile: IssuerRoutingProfile,
        key_numeric_facts: dict[str, list[PacketFact]],
        key_narrative_facts: dict[str, list[PacketNarrative]],
    ) -> list[str]:
        gaps: list[str] = []
        metrics = {metric.name for metric in summary.key_metrics}

        required_metrics = (
            "operating_profit",
            "profit_before_tax",
            "cash_and_cash_equivalents",
            "total_equity",
        )
        for metric in required_metrics:
            if metric not in metrics:
                gaps.append(f"Missing key metric: {metric}")

        if routing_profile.ivf_eligibility != "IVF_ELIGIBLE":
            gaps.append(
                f"Issuer is not currently IVF-eligible: {routing_profile.issuer_archetype}"
            )

        if not key_narrative_facts["going_concern_and_audit_evidence"]:
            gaps.append("No tagged going concern or audit narrative was found.")
        if not key_narrative_facts["debt_and_liquidity_evidence"]:
            gaps.append("No tagged debt or liquidity narrative was found.")
        if not key_numeric_facts["debt_liquidity_facts"]:
            gaps.append("No candidate debt or liquidity numeric facts were found.")

        return gaps
