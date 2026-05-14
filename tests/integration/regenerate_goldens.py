"""
Run this script to regenerate all golden files after an intentional output change:
    python tests/integration/regenerate_goldens.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from research_platform.documents.ixbrl_extractor import IXBRLExtractor
from research_platform.documents.ixbrl_summary import IXBRLFactSetBuilder

REPO_ROOT = Path(__file__).parent.parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "golden"

XHTML = {
    "tesco": REPO_ROOT / "data/downloads/nsm/tesco/NI-000119835_2138002P5RNKC5W2JZ46-2025-02-22/2138002P5RNKC5W2JZ46-2025-02-22/reports/2138002P5RNKC5W2JZ46-2025-02-22-T01.xhtml",
    "gym": REPO_ROOT / "data/downloads/nsm/the-gym-group/NI-000140727_213800VCU9TBANZIN455-2025-12-31/213800VCU9TBANZIN455-2025-12-31/reports/213800VCU9TBANZIN455-2025-12-31-T01.xhtml",
    "greencoat": REPO_ROOT / "data/downloads/nsm/greencoat-uk-wind/NI-000140433_213800ZPBBK8H51RX165-2025-12-31/213800ZPBBK8H51RX165-2025-12-31/reports/213800ZPBBK8H51RX165-2025-12-31-T01.xhtml",
}


def main():
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    extractor = IXBRLExtractor()
    builder = IXBRLFactSetBuilder()

    for name, path in XHTML.items():
        if not path.exists():
            print(f"SKIP {name}: {path} not found")
            continue

        extraction = extractor.extract(path)
        fact_set = builder.build(extraction)

        (GOLDEN_DIR / f"{name}_extraction_stats.json").write_text(
            json.dumps({
                "numeric_fact_count": extraction.numeric_fact_count,
                "narrative_fact_count": extraction.narrative_fact_count,
                "context_count": extraction.context_count,
            }, indent=2),
            encoding="utf-8",
        )

        (GOLDEN_DIR / f"{name}_fact_set.json").write_text(
            json.dumps({
                "entity": fact_set.entity,
                "latest_duration_end_date": fact_set.latest_duration_end_date,
                "latest_instant_date": fact_set.latest_instant_date,
                "numeric_fact_count": len(fact_set.numeric_facts),
                "narrative_fact_count": len(fact_set.narrative_facts),
                "top_numeric_concepts": [f.concept for f in fact_set.numeric_facts[:10]],
                "top_narrative_concepts": [f.concept for f in fact_set.narrative_facts[:5]],
            }, indent=2),
            encoding="utf-8",
        )

        print(
            f"{name}: {extraction.numeric_fact_count} numeric, "
            f"{extraction.narrative_fact_count} narrative facts"
        )

    print("Done.")


if __name__ == "__main__":
    main()
