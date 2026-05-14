"""
Golden file tests against real XHTML downloads.

Run with:  pytest tests/integration/ -m integration
Skip with: pytest tests/unit/   (default; integration tests not collected)

To regenerate golden files after an intentional output change:
    python tests/integration/regenerate_goldens.py
"""

import json
from pathlib import Path

import pytest

from research_platform.documents.ixbrl_extractor import IXBRLExtractor
from research_platform.documents.ixbrl_summary import IXBRLFactSetBuilder

REPO_ROOT = Path(__file__).parent.parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "golden"

XHTML = {
    "tesco": REPO_ROOT / "data/downloads/nsm/tesco/NI-000119835_2138002P5RNKC5W2JZ46-2025-02-22/2138002P5RNKC5W2JZ46-2025-02-22/reports/2138002P5RNKC5W2JZ46-2025-02-22-T01.xhtml",
    "gym": REPO_ROOT / "data/downloads/nsm/the-gym-group/NI-000140727_213800VCU9TBANZIN455-2025-12-31/213800VCU9TBANZIN455-2025-12-31/reports/213800VCU9TBANZIN455-2025-12-31-T01.xhtml",
    "greencoat": REPO_ROOT / "data/downloads/nsm/greencoat-uk-wind/NI-000140433_213800ZPBBK8H51RX165-2025-12-31/213800ZPBBK8H51RX165-2025-12-31/reports/213800ZPBBK8H51RX165-2025-12-31-T01.xhtml",
}


def load_golden(name: str) -> dict:
    return json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))


def xhtml_available(key: str) -> bool:
    return XHTML[key].exists()


# ---------------------------------------------------------------------------
# Extraction stats
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not xhtml_available("tesco"), reason="Tesco XHTML not present")
def test_tesco_extraction_stats_match_golden():
    extraction = IXBRLExtractor().extract(XHTML["tesco"])
    actual = {
        "numeric_fact_count": extraction.numeric_fact_count,
        "narrative_fact_count": extraction.narrative_fact_count,
        "context_count": extraction.context_count,
    }
    assert actual == load_golden("tesco_extraction_stats.json")


@pytest.mark.integration
@pytest.mark.skipif(not xhtml_available("gym"), reason="Gym Group XHTML not present")
def test_gym_extraction_stats_match_golden():
    extraction = IXBRLExtractor().extract(XHTML["gym"])
    actual = {
        "numeric_fact_count": extraction.numeric_fact_count,
        "narrative_fact_count": extraction.narrative_fact_count,
        "context_count": extraction.context_count,
    }
    assert actual == load_golden("gym_extraction_stats.json")


@pytest.mark.integration
@pytest.mark.skipif(not xhtml_available("greencoat"), reason="Greencoat XHTML not present")
def test_greencoat_extraction_stats_match_golden():
    extraction = IXBRLExtractor().extract(XHTML["greencoat"])
    actual = {
        "numeric_fact_count": extraction.numeric_fact_count,
        "narrative_fact_count": extraction.narrative_fact_count,
        "context_count": extraction.context_count,
    }
    assert actual == load_golden("greencoat_extraction_stats.json")


# ---------------------------------------------------------------------------
# Fact set (dedup counts + top concepts)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not xhtml_available("tesco"), reason="Tesco XHTML not present")
def test_tesco_fact_set_matches_golden():
    extraction = IXBRLExtractor().extract(XHTML["tesco"])
    fact_set = IXBRLFactSetBuilder().build(extraction)
    actual = {
        "entity": fact_set.entity,
        "latest_duration_end_date": fact_set.latest_duration_end_date,
        "latest_instant_date": fact_set.latest_instant_date,
        "numeric_fact_count": len(fact_set.numeric_facts),
        "narrative_fact_count": len(fact_set.narrative_facts),
        "top_numeric_concepts": [f.concept for f in fact_set.numeric_facts[:10]],
        "top_narrative_concepts": [f.concept for f in fact_set.narrative_facts[:5]],
    }
    assert actual == load_golden("tesco_fact_set.json")


@pytest.mark.integration
@pytest.mark.skipif(not xhtml_available("gym"), reason="Gym Group XHTML not present")
def test_gym_fact_set_matches_golden():
    extraction = IXBRLExtractor().extract(XHTML["gym"])
    fact_set = IXBRLFactSetBuilder().build(extraction)
    actual = {
        "entity": fact_set.entity,
        "latest_duration_end_date": fact_set.latest_duration_end_date,
        "latest_instant_date": fact_set.latest_instant_date,
        "numeric_fact_count": len(fact_set.numeric_facts),
        "narrative_fact_count": len(fact_set.narrative_facts),
        "top_numeric_concepts": [f.concept for f in fact_set.numeric_facts[:10]],
        "top_narrative_concepts": [f.concept for f in fact_set.narrative_facts[:5]],
    }
    assert actual == load_golden("gym_fact_set.json")


@pytest.mark.integration
@pytest.mark.skipif(not xhtml_available("greencoat"), reason="Greencoat XHTML not present")
def test_greencoat_fact_set_matches_golden():
    extraction = IXBRLExtractor().extract(XHTML["greencoat"])
    fact_set = IXBRLFactSetBuilder().build(extraction)
    actual = {
        "entity": fact_set.entity,
        "latest_duration_end_date": fact_set.latest_duration_end_date,
        "latest_instant_date": fact_set.latest_instant_date,
        "numeric_fact_count": len(fact_set.numeric_facts),
        "narrative_fact_count": len(fact_set.narrative_facts),
        "top_numeric_concepts": [f.concept for f in fact_set.numeric_facts[:10]],
        "top_narrative_concepts": [f.concept for f in fact_set.narrative_facts[:5]],
    }
    assert actual == load_golden("greencoat_fact_set.json")


