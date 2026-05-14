from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LLMResponse:
    text: str
    provider: str
    model: str


class LLMClient:
    provider_name: str

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        response_schema: dict | None = None,
    ) -> LLMResponse:
        raise NotImplementedError
