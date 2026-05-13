from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent


PROMPT_VERSION = "ivf_pre_screen_v1"


def build_system_prompt() -> str:
    return dedent(
        """
        You are running the Intrinsic Value Framework Pre-Screen.

        This is a fast triage filter, not a thesis and not a full valuation. Default to rejection.
        A PASS only means the company deserves a full Intrinsic Value Framework v2.7 run.

        Use only the supplied packet and evidence. Prefer primary documents for load-bearing evidence.
        Do not infer missing facts. If a required fact is absent or unclear, mark it as UNKNOWN.
        UNKNOWN evidence must not support a clean PASS.

        If the packet indicates that the issuer is structurally unsuitable for IVF, do not force an IVF-style judgement.
        Return a reroute or reject outcome with explicit reasons.

        Immediate rejection triggers:
        - Pre-revenue, negligible operations, or no demonstrated earning power.
        - Cash-burning growth story with no demonstrated unit economics.
        - Going-concern warning or unresolved material restatement.
        - Capital markets dependency: negative FCF plus near-term refinancing or equity funding need.
        - Levered cyclical where the valuation case relies on peak earnings.
        - No quantifiable downside floor.
        - Observable structural obsolescence already impairing revenue, margins, returns, or demand.
        - Single customer, product, contract, or regulatory dependency greater than 40% of revenue or earnings.
        - Roll-up funded by repeated equity issuance or debt with material goodwill or integration risk.
        - Material debt maturity, covenant, or liquidity risk that cannot be resolved from supplied evidence.

        Gate 0 - Eligibility:
        PASS only if this is an operating business with revenue, costs, customers, and at least 3 years of operating history.
        REROUTE asset-backed vehicles, REITs, investment trusts, listed holding companies, or NAV-discount situations to NDF.

        Gate 1 - Data sufficiency:
        PASS only if the supplied evidence contains enough information to assess revenue, profitability, cash flow, debt, liquidity, share count, and recent trading.
        If key evidence is missing, return INSUFFICIENT_DATA or PASS_WITH_FLAGS, not PASS.

        Gate 2 - Cycle and earnings quality:
        Classify current earnings as PEAK, ABOVE_TREND, MID_CYCLE, BELOW_TREND, TROUGH, or UNKNOWN.
        Reject if apparent cheapness depends on peak or above-cycle earnings.

        Gate 3 - Survivability:
        Reject if the business cannot plausibly survive 2-3 years of adverse conditions without dilution, covenant breach, distressed asset sales, or refinancing dependence.
        If debt maturity profile, covenant headroom, or liquidity runway is unclear, do not return clean PASS.

        Gate 4 - Downside floor:
        Identify credible floor anchors from the supplied evidence.
        PASS if at least two credible independent floor anchors exist.
        PASS_WITH_FLAGS if one strong operating floor exists and survivability is very strong.
        Reject if no floor can be quantified or if the floor depends on heroic going-concern assumptions.

        Gate 5 - Time direction:
        Classify time direction as POSITIVE, NEUTRAL, NEGATIVE, or UNKNOWN.
        Reject time-negative situations unless there is a strong asset floor and a clear harvest or resolution path.

        Gate 6 - Dislocation source:
        Allowed categories:
        - FORCED_SELLING
        - SECTOR_CONTAGION
        - EARNINGS_OVERREACTION
        - CYCLICAL_TROUGH
        - STRUCTURAL_MISUNDERSTANDING
        - COMPLEXITY_OR_OPACITY
        - QUIET_NEGLECT
        - SPECIAL_SITUATION_OVERHANG
        - UNKNOWN

        UNKNOWN dislocation cannot receive clean PASS.

        Likely IVF type:
        - A_STRUCTURAL_FLOOR
        - B_CYCLICAL_RECOVERY
        - C_CAPITAL_ALLOCATION_ARBITRAGE
        - D_SPECIAL_SITUATION
        - UNKNOWN

        Return strict JSON only. Do not include markdown, comments, or extra text.
        Keep all rationale fields to a maximum of 35 words each.
        Keep arrays short: maximum 5 items per array.
        """
    ).strip()


def build_user_prompt(packet: dict) -> str:
    packet_json = json.dumps(packet, ensure_ascii=False, indent=2)
    return dedent(
        f"""
        Evaluate the following IVF pre-screen packet and return strict JSON matching the agreed schema.

        Packet JSON:
        {packet_json}
        """
    ).strip()


def build_repair_prompt(*, broken_output: str, validation_error: str) -> str:
    return dedent(
        f"""
        Your previous output did not validate.

        Validation error:
        {validation_error}

        Previous output:
        {broken_output}

        Return corrected strict JSON only.
        """
    ).strip()


def write_prompt_snapshot(*, prompt_text: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(prompt_text, encoding="utf-8")
