from __future__ import annotations

from pydantic import BaseModel, Field

from research_platform.documents.ixbrl_extractor import IXBRLExtractionResult
from research_platform.documents.ixbrl_summary import IXBRLSummaryResult


class IssuerRoutingProfile(BaseModel):
    issuer_archetype: str
    ivf_eligibility: str
    preferred_next_framework: str | None = None
    ineligibility_reasons: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)


class IssuerRouter:
    def route(
        self,
        summary: IXBRLSummaryResult,
        extraction: IXBRLExtractionResult,
    ) -> IssuerRoutingProfile:
        metrics = {metric.name: metric for metric in summary.key_metrics}
        revenue = metrics.get("revenue")
        gross_profit = metrics.get("gross_profit")

        narrative_text = " ".join(
            fact.text or ""
            for fact in extraction.facts
            if fact.fact_type == "narrative" and fact.text
        ).lower()

        signals: list[str] = []
        reasons: list[str] = []

        investment_signals = (
            "investment manager",
            "investment objective",
            "net asset value",
            "portfolio",
            "dividend target",
            "wind farm",
        )
        financial_signals = (
            "banking operations",
            "customer deposits",
            "insurance contracts",
            "capital adequacy",
        )

        if any(signal in narrative_text for signal in investment_signals) and revenue is None:
            signals.append("investment_vehicle_language_detected")
            reasons.append(
                "Issuer appears to be an investment or asset-backed vehicle rather than an operating company."
            )
            return IssuerRoutingProfile(
                issuer_archetype="INVESTMENT_TRUST_OR_ASSET_BACKED_VEHICLE",
                ivf_eligibility="IVF_INELIGIBLE",
                preferred_next_framework="OTHER",
                ineligibility_reasons=reasons,
                signals=signals,
            )

        if revenue is not None or gross_profit is not None:
            signals.append("operating_metrics_detected")
            return IssuerRoutingProfile(
                issuer_archetype="OPERATING_COMPANY",
                ivf_eligibility="IVF_ELIGIBLE",
                preferred_next_framework="IVF_PRE_SCREEN",
                signals=signals,
            )

        if any(signal in narrative_text for signal in financial_signals):
            signals.append("financial_institution_language_detected")
            reasons.append(
                "Issuer appears to have financial-institution-style risk and balance-sheet characteristics."
            )
            return IssuerRoutingProfile(
                issuer_archetype="FINANCIAL_INSTITUTION_OR_INSURANCE",
                ivf_eligibility="IVF_INELIGIBLE",
                preferred_next_framework="OTHER",
                ineligibility_reasons=reasons,
                signals=signals,
            )

        signals.append("insufficient_operating_signals")
        reasons.append(
            "No clear operating-company revenue or gross-profit signal was found in the latest iXBRL summary."
        )
        return IssuerRoutingProfile(
            issuer_archetype="MANUAL_REVIEW",
            ivf_eligibility="MANUAL_REVIEW",
            preferred_next_framework=None,
            ineligibility_reasons=reasons,
            signals=signals,
        )
