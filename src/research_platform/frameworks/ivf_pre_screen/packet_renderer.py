from __future__ import annotations

import re


MAX_NARRATIVE_CHARS = 1000
MAX_DURATION_ROWS = 40
MAX_INSTANT_ROWS = 40
MAX_NARRATIVE_SECTIONS = 20


def render_packet_for_prompt(packet: dict) -> str:
    sections = [
        _render_header(packet),
        _render_recency(packet),
        _render_income_statement(packet),
        _render_balance_sheet(packet),
        _render_narratives(packet),
        _render_evidence_gaps(packet),
    ]
    return "\n\n".join(s for s in sections if s)


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_recency(packet: dict) -> str:
    rec = packet.get("recency", {})
    if not rec:
        return ""

    lines = ["### Data Recency"]

    annual_end = rec.get("annual_period_end")
    annual_age = rec.get("annual_age_months")
    if annual_end:
        age_str = f" ({annual_age} months ago)" if annual_age is not None else ""
        lines.append(f"- Annual report period end: {_fmt_date(annual_end)}{age_str}")

    if rec.get("post_period_update_available"):
        post_end = rec.get("post_period_end")
        post_age = rec.get("post_period_age_months")
        post_type = rec.get("post_period_type") or "post-period update"
        age_str = f" ({post_age} months ago)" if post_age is not None else ""
        date_str = f": {_fmt_date(post_end)}{age_str}" if post_end else ""
        lines.append(f"- Post-period update available ({post_type}){date_str}")
    else:
        if rec.get("is_stale"):
            lines.append(
                "- **NO POST-PERIOD UPDATE SUPPLIED** — developments since the annual "
                "report period end are not reflected in this packet."
            )
        else:
            lines.append("- No post-period update supplied.")

    return "\n".join(lines)


def _render_header(packet: dict) -> str:
    meta = packet.get("report_metadata", {})
    entity = meta.get("entity") or "Unknown"
    end_date = meta.get("latest_duration_end_date") or ""
    instant_date = meta.get("latest_instant_date") or ""

    lines = ["## IVF Pre-Screen Packet"]
    lines.append(f"Entity: {entity}")
    if end_date:
        lines.append(f"Latest annual period end: {_fmt_date(end_date)}")
    if instant_date:
        lines.append(f"Latest balance sheet date: {_fmt_date(instant_date)}")
    lines.append(
        f"Facts: {meta.get('numeric_fact_count', 0)} numeric, "
        f"{meta.get('narrative_fact_count', 0)} narrative"
    )
    return "\n".join(lines)


def _render_income_statement(packet: dict) -> str:
    # Duration facts only, dimensionless only
    facts = [
        f for f in packet.get("numeric_facts", [])
        if "endDate" in f.get("period", {}) and not f.get("dimensions")
    ]
    if not facts:
        return ""

    end_dates = sorted({f["period"]["endDate"] for f in facts}, reverse=True)[:2]
    latest, prior = end_dates[0], (end_dates[1] if len(end_dates) > 1 else None)

    by_concept: dict[str, dict[str, dict]] = {}
    for fact in facts:
        date = fact["period"]["endDate"]
        if date not in end_dates:
            continue
        by_concept.setdefault(fact["concept"], {})[date] = fact

    # Sort by absolute value of latest figure descending — surfaces major P&L lines first
    ranked = sorted(
        by_concept.items(),
        key=lambda kv: abs((kv[1].get(latest) or {}).get("value") or 0),
        reverse=True,
    )[:MAX_DURATION_ROWS]

    if not ranked:
        return ""

    latest_col = _fmt_period_col(latest, "duration")
    rows = ["### Income Statement"]
    if prior:
        prior_col = _fmt_period_col(prior, "duration")
        rows += [f"| Concept | {latest_col} | {prior_col} |", "|---------|---------|---------|"]
    else:
        rows += [f"| Concept | {latest_col} |", "|---------|---------|"]

    for concept, period_facts in ranked:
        label = _concept_label(concept)
        latest_str = _fmt_value(period_facts.get(latest))
        if prior:
            prior_str = _fmt_value(period_facts.get(prior))
            rows.append(f"| {label} | {latest_str} | {prior_str} |")
        else:
            rows.append(f"| {label} | {latest_str} |")

    return "\n".join(rows)


def _render_balance_sheet(packet: dict) -> str:
    # Instant facts only, dimensionless only
    facts = [
        f for f in packet.get("numeric_facts", [])
        if "instant" in f.get("period", {}) and not f.get("dimensions")
    ]
    if not facts:
        return ""

    instant_dates = sorted({f["period"]["instant"] for f in facts}, reverse=True)[:2]
    latest, prior = instant_dates[0], (instant_dates[1] if len(instant_dates) > 1 else None)

    by_concept: dict[str, dict[str, dict]] = {}
    for fact in facts:
        date = fact["period"]["instant"]
        if date not in instant_dates:
            continue
        by_concept.setdefault(fact["concept"], {})[date] = fact

    ranked = sorted(
        by_concept.items(),
        key=lambda kv: abs((kv[1].get(latest) or {}).get("value") or 0),
        reverse=True,
    )[:MAX_INSTANT_ROWS]

    if not ranked:
        return ""

    latest_col = _fmt_period_col(latest, "instant")
    rows = ["### Balance Sheet"]
    if prior:
        prior_col = _fmt_period_col(prior, "instant")
        rows += [f"| Concept | {latest_col} | {prior_col} |", "|---------|---------|---------|"]
    else:
        rows += [f"| Concept | {latest_col} |", "|---------|---------|"]

    for concept, period_facts in ranked:
        label = _concept_label(concept)
        latest_str = _fmt_value(period_facts.get(latest))
        if prior:
            prior_str = _fmt_value(period_facts.get(prior))
            rows.append(f"| {label} | {latest_str} | {prior_str} |")
        else:
            rows.append(f"| {label} | {latest_str} |")

    return "\n".join(rows)


def _render_narratives(packet: dict) -> str:
    # Already sorted longest-first by the fact set builder; take the top N
    narrative_facts = packet.get("narrative_facts", [])[:MAX_NARRATIVE_SECTIONS]
    if not narrative_facts:
        return ""

    seen: set[str] = set()
    lines = ["### Key Disclosures"]

    for fact in narrative_facts:
        concept = fact.get("concept") or ""
        text = fact.get("text") or ""
        if not concept or not text or concept in seen:
            continue
        seen.add(concept)

        label = _concept_label(concept)
        if len(text) > MAX_NARRATIVE_CHARS:
            text = text[:MAX_NARRATIVE_CHARS].rstrip() + "…"

        lines.append(f"\n**{label}**")
        lines.append(text)

    return "\n".join(lines) if len(lines) > 1 else ""


def _render_evidence_gaps(packet: dict) -> str:
    gaps = packet.get("evidence_gaps", [])
    if not gaps:
        return ""
    lines = ["### Evidence Gaps"]
    lines += [f"- {gap}" for gap in gaps]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _concept_label(concept: str) -> str:
    name = concept.split(":")[-1] if ":" in concept else concept
    # Insert space before uppercase letters that follow lowercase
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    # Insert space between consecutive uppercase runs and the next capitalised word
    name = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    return name


def _fmt_value(fact: dict | None) -> str:
    if fact is None:
        return "—"
    value = fact.get("value")
    if value is None:
        return "—"

    # Normalise unit: strip namespace prefix (e.g. iso4217:GBP → GBP)
    raw_unit = fact.get("unit") or ""
    unit = raw_unit.split(":")[-1] if ":" in raw_unit else raw_unit

    currency = {"GBP": "£", "USD": "$", "EUR": "€"}.get(unit)
    abs_val = abs(value)
    sign = "-" if value < 0 else ""

    if currency:
        if abs_val >= 1_000_000_000:
            return f"{sign}{currency}{abs_val / 1_000_000_000:.1f}bn"
        elif abs_val >= 1_000_000:
            return f"{sign}{currency}{abs_val / 1_000_000:.0f}m"
        elif abs_val >= 1_000:
            return f"{sign}{currency}{abs_val / 1_000:.1f}k"
        else:
            return f"{sign}{currency}{abs_val:.2f}"
    else:
        if abs_val >= 1_000_000_000:
            return f"{sign}{abs_val / 1_000_000_000:.2f}bn"
        elif abs_val >= 1_000_000:
            return f"{sign}{abs_val / 1_000_000:.1f}m"
        elif abs_val >= 1:
            return f"{sign}{abs_val:.2f}"
        else:
            return f"{sign}{abs_val:.4f}"


def _fmt_date(date_str: str) -> str:
    try:
        from datetime import date
        d = date.fromisoformat(date_str)
        return f"{d.day} {d.strftime('%b')} {d.year}"
    except ValueError:
        return date_str


def _fmt_period_col(date_str: str, period_type: str) -> str:
    try:
        from datetime import date
        d = date.fromisoformat(date_str)
        month_year = f"{d.strftime('%b')} {d.year}"
        return f"Year to {month_year}" if period_type == "duration" else f"At {month_year}"
    except ValueError:
        return date_str
