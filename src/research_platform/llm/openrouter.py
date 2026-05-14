from __future__ import annotations

import json

import httpx

from research_platform.llm.base import LLMClient, LLMResponse


class OpenRouterClient(LLMClient):
    provider_name = "openrouter"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: int,
        http_referer: str | None,
        app_title: str,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.http_referer = http_referer
        self.app_title = app_title

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        response_schema: dict | None = None,
    ) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": self.app_title,
        }
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer

        if response_schema is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": response_schema, "strict": True},
            }
        else:
            response_format = {"type": "json_object"}

        payload = {
            "model": model,
            "temperature": temperature,
            "response_format": response_format,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices", [])
        if not choices:
            raise ValueError("OpenRouter returned no choices.")

        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text", "")))
            content = "".join(text_parts)
        elif isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False)

        return LLMResponse(text=str(content).strip(), provider=self.provider_name, model=model)
