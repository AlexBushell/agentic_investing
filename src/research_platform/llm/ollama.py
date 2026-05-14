from __future__ import annotations

import json

import httpx

from research_platform.llm.base import LLMClient, LLMResponse


class OllamaClient(LLMClient):
    provider_name = "ollama"

    def __init__(self, *, base_url: str, timeout_seconds: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        response_schema: dict | None = None,
    ) -> LLMResponse:
        payload = {
            "model": model,
            "stream": False,
            "format": response_schema if response_schema is not None else "json",
            "options": {
                "temperature": temperature,
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()

        message = data.get("message", {})
        content = message.get("content", "")
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False)

        return LLMResponse(text=str(content).strip(), provider=self.provider_name, model=model)
