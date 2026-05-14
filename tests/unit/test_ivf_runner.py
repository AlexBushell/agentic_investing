import json
from unittest.mock import MagicMock, call

import pytest
from pydantic import ValidationError

from research_platform.frameworks.ivf_pre_screen.runner import IVFPreScreenRunner
from research_platform.frameworks.ivf_pre_screen.schema import IVFPreScreenResult
from research_platform.llm.base import LLMResponse


VALID_RESULT = {
    "name": "Test Co",
    "ticker": "TST",
    "sector": "Retail",
    "status": "PASS",
    "confidence": "MEDIUM",
    "target_framework": "IVF",
    "killed_at_gate": None,
    "primary_decision_rationale": "Passes all gates on available evidence.",
    "gate_results": {
        "gate_0_eligibility": {"result": "PASS", "rationale": "Operating company with revenue."},
        "gate_1_data_sufficiency": {"result": "PASS", "rationale": "Sufficient evidence.", "missing_evidence": []},
        "gate_2_cycle_and_earnings_quality": {"result": "PASS", "rationale": "Mid-cycle.", "cycle_position": "MID_CYCLE"},
        "gate_3_survivability": {"result": "PASS", "rationale": "Strong balance sheet.", "key_risks": []},
        "gate_4_downside_floor": {"result": "PASS", "rationale": "Two floor anchors.", "floor_anchors": ["Asset backing", "Earnings floor"]},
        "gate_5_time_direction": {"result": "PASS", "rationale": "Positive trajectory.", "time_direction": "POSITIVE"},
        "gate_6_dislocation_source": {"result": "PASS", "rationale": "Clear dislocation.", "dislocation_source": "CYCLICAL_TROUGH"},
    },
    "immediate_rejection_triggers_found": [],
    "flags": [],
    "evidence_gaps": [],
    "likely_ivf_type": "B_CYCLICAL_RECOVERY",
    "recommended_next_step": "FULL_IVF_RUN",
    "one_sentence_summary": "Solid operating company at cyclical trough with identifiable floor.",
}

VALID_RESULT_JSON = json.dumps(VALID_RESULT)

INVALID_JSON = '{"status": "NOT_A_VALID_STATUS", "name": "Test Co"}'

SAMPLE_PACKET = {"packet_type": "IVF_PRE_SCREEN_IXBRL_V1", "issuer_routing_profile": {}}


def make_llm_client(*responses: str) -> MagicMock:
    client = MagicMock()
    client.provider_name = "mock"
    client.generate_json.side_effect = [
        LLMResponse(text=r, provider="mock", model="mock-model") for r in responses
    ]
    return client


@pytest.fixture
def runner_factory():
    def _make(llm_client, max_repair_attempts=1):
        return IVFPreScreenRunner(
            llm_client=llm_client,
            model="mock-model",
            temperature=0.1,
            max_repair_attempts=max_repair_attempts,
        )
    return _make


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestRunnerHappyPath:
    def test_valid_response_returned_immediately(self, runner_factory):
        client = make_llm_client(VALID_RESULT_JSON)
        runner = runner_factory(client)
        result = runner.run(packet=SAMPLE_PACKET)
        assert isinstance(result, IVFPreScreenResult)
        assert result.status == "PASS"
        assert client.generate_json.call_count == 1

    def test_result_fields_match_response(self, runner_factory):
        client = make_llm_client(VALID_RESULT_JSON)
        result = runner_factory(client).run(packet=SAMPLE_PACKET)
        assert result.name == "Test Co"
        assert result.confidence == "MEDIUM"
        assert result.likely_ivf_type == "B_CYCLICAL_RECOVERY"

    def test_llm_called_with_system_and_user_prompt(self, runner_factory):
        client = make_llm_client(VALID_RESULT_JSON)
        runner_factory(client).run(packet=SAMPLE_PACKET)
        kwargs = client.generate_json.call_args.kwargs
        assert "system_prompt" in kwargs
        assert "user_prompt" in kwargs
        assert len(kwargs["system_prompt"]) > 0
        assert len(kwargs["user_prompt"]) > 0


# ---------------------------------------------------------------------------
# Repair path
# ---------------------------------------------------------------------------


class TestRunnerRepair:
    def test_invalid_first_response_triggers_repair(self, runner_factory):
        client = make_llm_client(INVALID_JSON, VALID_RESULT_JSON)
        result = runner_factory(client, max_repair_attempts=1).run(packet=SAMPLE_PACKET)
        assert isinstance(result, IVFPreScreenResult)
        assert client.generate_json.call_count == 2

    def test_repair_prompt_references_broken_output(self, runner_factory):
        client = make_llm_client(INVALID_JSON, VALID_RESULT_JSON)
        runner_factory(client, max_repair_attempts=1).run(packet=SAMPLE_PACKET)
        repair_call_kwargs = client.generate_json.call_args_list[1].kwargs
        assert INVALID_JSON in repair_call_kwargs["user_prompt"]

    def test_repair_prompt_references_validation_error(self, runner_factory):
        client = make_llm_client(INVALID_JSON, VALID_RESULT_JSON)
        runner_factory(client, max_repair_attempts=1).run(packet=SAMPLE_PACKET)
        repair_call_kwargs = client.generate_json.call_args_list[1].kwargs
        assert len(repair_call_kwargs["user_prompt"]) > len(INVALID_JSON)

    def test_two_repair_attempts_succeed_on_second(self, runner_factory):
        client = make_llm_client(INVALID_JSON, INVALID_JSON, VALID_RESULT_JSON)
        result = runner_factory(client, max_repair_attempts=2).run(packet=SAMPLE_PACKET)
        assert isinstance(result, IVFPreScreenResult)
        assert client.generate_json.call_count == 3


# ---------------------------------------------------------------------------
# Failure path — exhausted repair attempts
# ---------------------------------------------------------------------------


class TestRunnerFailure:
    def test_raises_after_max_repair_attempts_exceeded(self, runner_factory):
        client = make_llm_client(INVALID_JSON, INVALID_JSON)
        with pytest.raises(ValidationError):
            runner_factory(client, max_repair_attempts=1).run(packet=SAMPLE_PACKET)

    def test_no_repair_when_max_attempts_zero(self, runner_factory):
        client = make_llm_client(INVALID_JSON)
        with pytest.raises(ValidationError):
            runner_factory(client, max_repair_attempts=0).run(packet=SAMPLE_PACKET)
        assert client.generate_json.call_count == 1


# ---------------------------------------------------------------------------
# build_run_payload
# ---------------------------------------------------------------------------


class TestBuildRunPayload:
    def test_payload_contains_expected_keys(self, runner_factory):
        client = make_llm_client(VALID_RESULT_JSON)
        runner = runner_factory(client)
        result = runner.run(packet=SAMPLE_PACKET)
        payload = runner.build_run_payload(
            packet=SAMPLE_PACKET,
            result=result,
            provider="mock",
            model="mock-model",
        )
        assert payload["framework_code"] == "IVF_PRE_SCREEN"
        assert payload["provider"] == "mock"
        assert payload["model"] == "mock-model"
        assert payload["packet_type"] == "IVF_PRE_SCREEN_IXBRL_V1"
        assert "result" in payload

    def test_payload_result_is_serialisable(self, runner_factory):
        client = make_llm_client(VALID_RESULT_JSON)
        runner = runner_factory(client)
        result = runner.run(packet=SAMPLE_PACKET)
        payload = runner.build_run_payload(
            packet=SAMPLE_PACKET, result=result, provider="mock", model="mock-model"
        )
        json.dumps(payload)  # must not raise
