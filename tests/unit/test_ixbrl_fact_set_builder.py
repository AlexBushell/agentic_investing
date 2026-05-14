import pytest

from research_platform.documents.ixbrl_extractor import (
    IXBRLContext,
    IXBRLExtractionResult,
    IXBRLFact,
)
from research_platform.documents.ixbrl_summary import IXBRLFactSetBuilder


def make_extraction(facts: list[IXBRLFact]) -> IXBRLExtractionResult:
    numeric = sum(1 for f in facts if f.fact_type == "numeric")
    narrative = sum(1 for f in facts if f.fact_type == "narrative")
    return IXBRLExtractionResult(
        file_path="test.xhtml",
        context_count=1,
        numeric_fact_count=numeric,
        narrative_fact_count=narrative,
        facts=facts,
    )


def make_numeric(
    concept: str,
    value: float,
    end_date: str | None = None,
    instant: str | None = None,
    dimensions: dict | None = None,
    entity: str | None = None,
) -> IXBRLFact:
    period = {}
    if end_date:
        period = {"startDate": "2023-01-01", "endDate": end_date}
    elif instant:
        period = {"instant": instant}
    return IXBRLFact(
        fact_type="numeric",
        concept=concept,
        value=value,
        raw_text=str(value),
        context=IXBRLContext(
            id="ctx1",
            entity=entity,
            period=period,
            dimensions=dimensions or {},
        ),
        context_ref="ctx1",
    )


def make_narrative(
    concept: str,
    text: str,
    end_date: str | None = None,
) -> IXBRLFact:
    period = {"startDate": "2023-01-01", "endDate": end_date} if end_date else {}
    return IXBRLFact(
        fact_type="narrative",
        concept=concept,
        text=text,
        context=IXBRLContext(id="ctx1", period=period, dimensions={}),
        context_ref="ctx1",
    )


@pytest.fixture
def builder():
    return IXBRLFactSetBuilder()


# ---------------------------------------------------------------------------
# Numeric deduplication
# ---------------------------------------------------------------------------


class TestNumericDedup:
    def test_identical_facts_deduplicated(self, builder):
        fact = make_numeric("ifrs-full:Revenue", 1000.0, end_date="2024-03-31")
        result = builder.build(make_extraction([fact, fact]))
        assert len(result.numeric_facts) == 1

    def test_same_concept_different_periods_both_kept(self, builder):
        f1 = make_numeric("ifrs-full:Revenue", 1000.0, end_date="2024-03-31")
        f2 = make_numeric("ifrs-full:Revenue", 900.0, end_date="2023-03-31")
        result = builder.build(make_extraction([f1, f2]))
        assert len(result.numeric_facts) == 2

    def test_same_concept_different_dimensions_both_kept(self, builder):
        f1 = make_numeric("ifrs-full:Revenue", 600.0, end_date="2024-03-31", dimensions={"dim:Segment": "A"})
        f2 = make_numeric("ifrs-full:Revenue", 400.0, end_date="2024-03-31", dimensions={"dim:Segment": "B"})
        result = builder.build(make_extraction([f1, f2]))
        assert len(result.numeric_facts) == 2

    def test_fact_with_none_value_excluded(self, builder):
        fact = IXBRLFact(fact_type="numeric", concept="ifrs-full:Revenue", value=None)
        result = builder.build(make_extraction([fact]))
        assert result.numeric_facts == []

    def test_fact_without_concept_excluded(self, builder):
        fact = IXBRLFact(fact_type="numeric", concept=None, value=100.0)
        result = builder.build(make_extraction([fact]))
        assert result.numeric_facts == []

    def test_narrative_facts_excluded_from_numeric(self, builder):
        fact = make_narrative("ifrs-full:GoingConcern", "All good.", end_date="2024-03-31")
        result = builder.build(make_extraction([fact]))
        assert result.numeric_facts == []


# ---------------------------------------------------------------------------
# Numeric sorting — latest period first
# ---------------------------------------------------------------------------


class TestNumericSorting:
    def test_latest_end_date_first(self, builder):
        f1 = make_numeric("ifrs-full:Revenue", 900.0, end_date="2023-03-31")
        f2 = make_numeric("ifrs-full:Revenue", 1000.0, end_date="2024-03-31")
        result = builder.build(make_extraction([f1, f2]))
        assert result.numeric_facts[0].period["endDate"] == "2024-03-31"

    def test_latest_instant_date_first(self, builder):
        f1 = make_numeric("ifrs-full:Assets", 500.0, instant="2023-03-31")
        f2 = make_numeric("ifrs-full:Assets", 600.0, instant="2024-03-31")
        result = builder.build(make_extraction([f1, f2]))
        assert result.numeric_facts[0].period["instant"] == "2024-03-31"


# ---------------------------------------------------------------------------
# Narrative deduplication — keep longest text per (concept, period)
# ---------------------------------------------------------------------------


class TestNarrativeDedup:
    def test_duplicate_concept_period_keeps_longest(self, builder):
        short = make_narrative("ifrs-full:GoingConcern", "Short.", end_date="2024-03-31")
        long = make_narrative("ifrs-full:GoingConcern", "Much longer narrative text here.", end_date="2024-03-31")
        result = builder.build(make_extraction([short, long]))
        assert len(result.narrative_facts) == 1
        assert result.narrative_facts[0].text == "Much longer narrative text here."

    def test_same_concept_different_periods_both_kept(self, builder):
        f1 = make_narrative("ifrs-full:GoingConcern", "Year one.", end_date="2024-03-31")
        f2 = make_narrative("ifrs-full:GoingConcern", "Year two.", end_date="2023-03-31")
        result = builder.build(make_extraction([f1, f2]))
        assert len(result.narrative_facts) == 2

    def test_fact_without_text_excluded(self, builder):
        fact = IXBRLFact(fact_type="narrative", concept="ifrs-full:GoingConcern", text=None)
        result = builder.build(make_extraction([fact]))
        assert result.narrative_facts == []

    def test_fact_without_concept_excluded(self, builder):
        fact = IXBRLFact(fact_type="narrative", concept=None, text="Some text.")
        result = builder.build(make_extraction([fact]))
        assert result.narrative_facts == []


# ---------------------------------------------------------------------------
# Narrative sorting — longest text first
# ---------------------------------------------------------------------------


class TestNarrativeSorting:
    def test_longest_narrative_first(self, builder):
        short = make_narrative("ifrs-full:LiquidityRisk", "Short.", end_date="2024-03-31")
        long = make_narrative("ifrs-full:GoingConcern", "A much longer piece of narrative disclosure text.", end_date="2024-03-31")
        result = builder.build(make_extraction([short, long]))
        assert result.narrative_facts[0].concept == "ifrs-full:GoingConcern"


# ---------------------------------------------------------------------------
# Date extraction
# ---------------------------------------------------------------------------


class TestDateExtraction:
    def test_latest_duration_end_date(self, builder):
        f1 = make_numeric("ifrs-full:Revenue", 900.0, end_date="2023-03-31")
        f2 = make_numeric("ifrs-full:Revenue", 1000.0, end_date="2024-03-31")
        result = builder.build(make_extraction([f1, f2]))
        assert result.latest_duration_end_date == "2024-03-31"

    def test_latest_instant_date(self, builder):
        f1 = make_numeric("ifrs-full:Assets", 500.0, instant="2023-03-31")
        f2 = make_numeric("ifrs-full:Assets", 600.0, instant="2024-03-31")
        result = builder.build(make_extraction([f1, f2]))
        assert result.latest_instant_date == "2024-03-31"

    def test_no_facts_gives_none_dates(self, builder):
        result = builder.build(make_extraction([]))
        assert result.latest_duration_end_date is None
        assert result.latest_instant_date is None


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------


class TestEntityExtraction:
    def test_entity_taken_from_first_fact_with_context(self, builder):
        fact = make_numeric("ifrs-full:Revenue", 1000.0, end_date="2024-03-31", entity="GB-123456")
        result = builder.build(make_extraction([fact]))
        assert result.entity == "GB-123456"

    def test_no_entity_when_no_context(self, builder):
        fact = IXBRLFact(fact_type="numeric", concept="ifrs-full:Revenue", value=100.0)
        result = builder.build(make_extraction([fact]))
        assert result.entity is None
