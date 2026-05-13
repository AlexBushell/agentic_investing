from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from research_platform.frameworks.ivf_pre_screen.prompt import (
    PROMPT_VERSION,
    build_repair_prompt,
    build_system_prompt,
    build_user_prompt,
    write_prompt_snapshot,
)
from research_platform.frameworks.ivf_pre_screen.schema import IVFPreScreenResult
from research_platform.llm.base import LLMClient


class IVFPreScreenRunner:
    def __init__(
        self,
        *,
        llm_client: LLMClient,
        model: str,
        temperature: float,
        max_repair_attempts: int = 1,
    ) -> None:
        self.llm_client = llm_client
        self.model = model
        self.temperature = temperature
        self.max_repair_attempts = max_repair_attempts

    def run(
        self,
        *,
        packet: dict,
        prompt_out: Path | None = None,
        raw_response_out: Path | None = None,
    ) -> IVFPreScreenResult:
        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(packet)

        if prompt_out is not None:
            snapshot = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}\n"
            write_prompt_snapshot(prompt_text=snapshot, out_path=prompt_out)

        response = self.llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=self.model,
            temperature=self.temperature,
        )
        if raw_response_out is not None:
            raw_response_out.parent.mkdir(parents=True, exist_ok=True)
            raw_response_out.write_text(response.text, encoding="utf-8")

        current_text = response.text
        for attempt in range(self.max_repair_attempts + 1):
            try:
                return IVFPreScreenResult.model_validate_json(current_text)
            except ValidationError as exc:
                if attempt >= self.max_repair_attempts:
                    raise
                repair_prompt = build_repair_prompt(
                    broken_output=current_text,
                    validation_error=str(exc),
                )
                repaired = self.llm_client.generate_json(
                    system_prompt=system_prompt,
                    user_prompt=repair_prompt,
                    model=self.model,
                    temperature=self.temperature,
                )
                current_text = repaired.text
                if raw_response_out is not None:
                    repaired_path = raw_response_out.with_name(
                        f"{raw_response_out.stem}_repair_{attempt + 1}{raw_response_out.suffix}"
                    )
                    repaired_path.write_text(repaired.text, encoding="utf-8")

        raise RuntimeError("Unreachable IVF pre-screen validation state.")

    @staticmethod
    def build_run_payload(
        *,
        packet: dict,
        result: IVFPreScreenResult,
        provider: str,
        model: str,
    ) -> dict:
        return {
            "framework_code": "IVF_PRE_SCREEN",
            "framework_version": "v1.0",
            "prompt_version": PROMPT_VERSION,
            "provider": provider,
            "model": model,
            "packet_type": packet.get("packet_type"),
            "result": json.loads(result.model_dump_json()),
        }
