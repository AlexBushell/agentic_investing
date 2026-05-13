from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Gate0Eligibility(BaseModel):
    result: Literal["PASS", "REJECT", "REROUTE", "UNKNOWN"]
    rationale: str


class Gate1DataSufficiency(BaseModel):
    result: Literal["PASS", "FAIL", "PARTIAL", "UNKNOWN"]
    rationale: str
    missing_evidence: list[str] = Field(default_factory=list, max_length=5)


class Gate2CycleAndEarningsQuality(BaseModel):
    result: Literal["PASS", "REJECT", "FLAG", "UNKNOWN"]
    cycle_position: Literal[
        "PEAK",
        "ABOVE_TREND",
        "MID_CYCLE",
        "BELOW_TREND",
        "TROUGH",
        "UNKNOWN",
    ]
    rationale: str


class Gate3Survivability(BaseModel):
    result: Literal["PASS", "REJECT", "FLAG", "UNKNOWN"]
    rationale: str
    key_risks: list[str] = Field(default_factory=list, max_length=5)


class Gate4DownsideFloor(BaseModel):
    result: Literal["PASS", "REJECT", "FLAG", "UNKNOWN"]
    floor_anchors: list[str] = Field(default_factory=list, max_length=5)
    rationale: str


class Gate5TimeDirection(BaseModel):
    result: Literal["PASS", "REJECT", "FLAG", "UNKNOWN"]
    time_direction: Literal["POSITIVE", "NEUTRAL", "NEGATIVE", "UNKNOWN"]
    rationale: str


class Gate6DislocationSource(BaseModel):
    result: Literal["PASS", "FLAG", "UNKNOWN"]
    dislocation_source: Literal[
        "FORCED_SELLING",
        "SECTOR_CONTAGION",
        "EARNINGS_OVERREACTION",
        "CYCLICAL_TROUGH",
        "STRUCTURAL_MISUNDERSTANDING",
        "COMPLEXITY_OR_OPACITY",
        "QUIET_NEGLECT",
        "SPECIAL_SITUATION_OVERHANG",
        "UNKNOWN",
    ]
    rationale: str


class IVFPreScreenGateResults(BaseModel):
    gate_0_eligibility: Gate0Eligibility
    gate_1_data_sufficiency: Gate1DataSufficiency
    gate_2_cycle_and_earnings_quality: Gate2CycleAndEarningsQuality
    gate_3_survivability: Gate3Survivability
    gate_4_downside_floor: Gate4DownsideFloor
    gate_5_time_direction: Gate5TimeDirection
    gate_6_dislocation_source: Gate6DislocationSource


class IVFPreScreenResult(BaseModel):
    name: str
    ticker: str | None = None
    sector: str | None = None
    status: Literal["REJECT", "REROUTE", "INSUFFICIENT_DATA", "PASS_WITH_FLAGS", "PASS"]
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    target_framework: Literal["IVF", "NDF", "OTHER"] | None = None
    killed_at_gate: Literal[
        "IMMEDIATE_REJECTION",
        "GATE_0",
        "GATE_1",
        "GATE_2",
        "GATE_3",
        "GATE_4",
        "GATE_5",
        "GATE_6",
    ] | None = None
    primary_decision_rationale: str
    gate_results: IVFPreScreenGateResults
    immediate_rejection_triggers_found: list[str] = Field(default_factory=list, max_length=5)
    flags: list[str] = Field(default_factory=list, max_length=5)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=5)
    likely_ivf_type: Literal[
        "A_STRUCTURAL_FLOOR",
        "B_CYCLICAL_RECOVERY",
        "C_CAPITAL_ALLOCATION_ARBITRAGE",
        "D_SPECIAL_SITUATION",
        "UNKNOWN",
    ]
    recommended_next_step: Literal[
        "FULL_IVF_RUN",
        "REROUTE_TO_NDF",
        "REJECT_NO_FURTHER_WORK",
        "REQUEST_MORE_EVIDENCE",
        "WATCHLIST_ONLY",
    ]
    one_sentence_summary: str
