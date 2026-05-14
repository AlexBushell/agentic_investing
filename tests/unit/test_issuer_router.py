import pytest

from research_platform.documents.ixbrl_extractor import (
    IXBRLContext,
    IXBRLExtractionResult,
    IXBRLFact,
)
from research_platform.routing.issuer_router import IssuerRouter


def make_extraction(facts: list[IXBRLFact]) -> IXBRLExtractionResult:
    return IXBRLExtractionResult(
        file_path="test.xhtml",
        context_count=1,
        numeric_fact_count=0,
        narrative_fact_count=0,
        facts=facts,
    )


def make_numeric(concept: str, value: float = 1000.0) -> IXBRLFact:
    return IXBRLFact(fact_type="numeric", concept=concept, value=value)


def make_narrative(text: str) -> IXBRLFact:
    return IXBRLFact(fact_type="narrative", concept="ifrs-full:Disclosure", text=text)


@pytest.fixture
def router():
    return IssuerRouter()


# ---------------------------------------------------------------------------
# _has_revenue_concept
# ---------------------------------------------------------------------------


class TestHasRevenueConcept:
    def test_ifrs_revenue(self, router):
        assert router._has_revenue_concept([make_numeric("ifrs-full:Revenue")]) is True

    def test_ifrs_turnover(self, router):
        assert router._has_revenue_concept([make_numeric("ifrs-full:Turnover")]) is True

    def test_ifrs_gross_profit(self, router):
        assert router._has_revenue_concept([make_numeric("ifrs-full:GrossProfit")]) is True

    def test_revenue_in_extension_concept(self, router):
        assert router._has_revenue_concept([make_numeric("acme:RevenueFromContracts")]) is True

    def test_non_revenue_concept_returns_false(self, router):
        assert router._has_revenue_concept([make_numeric("ifrs-full:Assets")]) is False

    def test_narrative_fact_ignored(self, router):
        narrative = IXBRLFact(fact_type="narrative", concept="ifrs-full:Revenue", text="revenue text")
        assert router._has_revenue_concept([narrative]) is False

    def test_numeric_with_none_value_ignored(self, router):
        fact = IXBRLFact(fact_type="numeric", concept="ifrs-full:Revenue", value=None)
        assert router._has_revenue_concept([fact]) is False

    def test_empty_facts_returns_false(self, router):
        assert router._has_revenue_concept([]) is False


# ---------------------------------------------------------------------------
# Routing — operating company
# ---------------------------------------------------------------------------


class TestOperatingCompanyRouting:
    def test_revenue_concept_routes_to_operating_company(self, router):
        result = router.route(make_extraction([make_numeric("ifrs-full:Revenue")]))
        assert result.issuer_archetype == "OPERATING_COMPANY"
        assert result.ivf_eligibility == "IVF_ELIGIBLE"
        assert result.preferred_next_framework == "IVF_PRE_SCREEN"

    def test_gross_profit_routes_to_operating_company(self, router):
        result = router.route(make_extraction([make_numeric("ifrs-full:GrossProfit")]))
        assert result.issuer_archetype == "OPERATING_COMPANY"

    def test_revenue_present_overrides_investment_narrative(self, router):
        facts = [
            make_numeric("ifrs-full:Revenue"),
            make_narrative("The portfolio objective is to maximise net asset value."),
        ]
        result = router.route(make_extraction(facts))
        assert result.issuer_archetype == "OPERATING_COMPANY"

    def test_operating_signal_in_signals_list(self, router):
        result = router.route(make_extraction([make_numeric("ifrs-full:Revenue")]))
        assert "operating_metrics_detected" in result.signals


# ---------------------------------------------------------------------------
# Routing — investment trust / asset-backed vehicle
# ---------------------------------------------------------------------------


class TestInvestmentVehicleRouting:
    def test_investment_language_no_revenue_routes_to_investment_trust(self, router):
        facts = [make_narrative("The investment objective is to generate income from a diversified portfolio.")]
        result = router.route(make_extraction(facts))
        assert result.issuer_archetype == "INVESTMENT_TRUST_OR_ASSET_BACKED_VEHICLE"
        assert result.ivf_eligibility == "IVF_INELIGIBLE"

    def test_net_asset_value_language_triggers(self, router):
        facts = [make_narrative("The company targets growth in net asset value over the long term.")]
        result = router.route(make_extraction(facts))
        assert result.issuer_archetype == "INVESTMENT_TRUST_OR_ASSET_BACKED_VEHICLE"

    def test_wind_farm_language_triggers(self, router):
        facts = [make_narrative("The company owns and operates a wind farm in the North Sea.")]
        result = router.route(make_extraction(facts))
        assert result.issuer_archetype == "INVESTMENT_TRUST_OR_ASSET_BACKED_VEHICLE"

    def test_investment_vehicle_is_ineligible_for_ivf(self, router):
        facts = [make_narrative("Portfolio of dividend target investments.")]
        result = router.route(make_extraction(facts))
        assert result.ivf_eligibility == "IVF_INELIGIBLE"
        assert result.preferred_next_framework == "OTHER"


# ---------------------------------------------------------------------------
# Routing — financial institution
# ---------------------------------------------------------------------------


class TestFinancialInstitutionRouting:
    def test_customer_deposits_language_routes_to_financial_institution(self, router):
        facts = [make_narrative("Customer deposits form the primary source of funding for banking operations.")]
        result = router.route(make_extraction(facts))
        assert result.issuer_archetype == "FINANCIAL_INSTITUTION_OR_INSURANCE"
        assert result.ivf_eligibility == "IVF_INELIGIBLE"

    def test_capital_adequacy_language_triggers(self, router):
        facts = [make_narrative("Capital adequacy requirements are met under Basel III.")]
        result = router.route(make_extraction(facts))
        assert result.issuer_archetype == "FINANCIAL_INSTITUTION_OR_INSURANCE"

    def test_insurance_contracts_language_triggers(self, router):
        facts = [make_narrative("The group issues insurance contracts to retail customers.")]
        result = router.route(make_extraction(facts))
        assert result.issuer_archetype == "FINANCIAL_INSTITUTION_OR_INSURANCE"


# ---------------------------------------------------------------------------
# Routing — manual review fallback
# ---------------------------------------------------------------------------


class TestManualReviewRouting:
    def test_no_signals_routes_to_manual_review(self, router):
        result = router.route(make_extraction([]))
        assert result.issuer_archetype == "MANUAL_REVIEW"
        assert result.ivf_eligibility == "MANUAL_REVIEW"
        assert result.preferred_next_framework is None

    def test_neutral_narrative_routes_to_manual_review(self, router):
        facts = [make_narrative("The directors present their annual report for the year ended 31 March 2024.")]
        result = router.route(make_extraction(facts))
        assert result.issuer_archetype == "MANUAL_REVIEW"

    def test_manual_review_has_reason(self, router):
        result = router.route(make_extraction([]))
        assert len(result.ineligibility_reasons) > 0
