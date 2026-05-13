from __future__ import annotations

from research_platform.core.config import Settings
from research_platform.llm.base import LLMClient
from research_platform.llm.ollama import OllamaClient
from research_platform.llm.openrouter import OpenRouterClient


def create_llm_client(settings: Settings) -> LLMClient:
    provider = settings.llm_provider.strip().lower()

    if provider == "ollama":
        return OllamaClient(
            base_url=settings.ollama_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    if provider == "openrouter":
        if not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter.")
        return OpenRouterClient(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
            timeout_seconds=settings.llm_timeout_seconds,
            http_referer=settings.openrouter_http_referer,
            app_title=settings.openrouter_app_title,
        )

    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
