"""LLM provider helpers — Azure OpenAI and Gemini (OpenAI-compatible API)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
AZURE_OPENAI_API_VERSION = "2024-12-01-preview"


@dataclass(frozen=True)
class LlmConfig:
    """Resolved credentials and provider for LLM calls."""

    provider: str  # "azure" | "gemini"
    api_key: str
    endpoint: Optional[str] = None  # Azure project endpoint only


def normalize_azure_endpoint(endpoint: str) -> str:
    if endpoint.endswith("/api"):
        return endpoint[:-4]
    return endpoint


def validate_llm_config(cfg: LlmConfig) -> Optional[str]:
    """Return an error message if configuration is incomplete."""
    if cfg.provider not in ("azure", "gemini"):
        return f"Unsupported AI provider: {cfg.provider!r} (use azure or gemini)"
    if not cfg.api_key:
        key = "AZURE_OPENAI_API_KEY" if cfg.provider == "azure" else "GOOGLE_API_KEY"
        return f"{cfg.provider} API key not configured (set {key})"
    if cfg.provider == "azure" and not cfg.endpoint:
        return "Azure endpoint not configured (set AZURE_AI_PROJECT_ENDPOINT)"
    return None


def create_async_chat_client(model: str, cfg: LlmConfig):
    """Create an agent-framework chat client for the configured provider."""
    from agent_framework.openai import OpenAIChatCompletionClient

    if cfg.provider == "gemini":
        return OpenAIChatCompletionClient(
            model=model,
            api_key=cfg.api_key,
            base_url=GEMINI_OPENAI_BASE_URL,
        )

    return OpenAIChatCompletionClient(
        model=model,
        azure_endpoint=normalize_azure_endpoint(cfg.endpoint or ""),
        api_key=cfg.api_key,
        api_version=AZURE_OPENAI_API_VERSION,
    )


def create_sync_chat_client(cfg: LlmConfig):
    """Create a synchronous OpenAI SDK client for web chat endpoints."""
    if cfg.provider == "gemini":
        from openai import OpenAI

        return OpenAI(api_key=cfg.api_key, base_url=GEMINI_OPENAI_BASE_URL)

    from openai import AzureOpenAI

    return AzureOpenAI(
        azure_endpoint=normalize_azure_endpoint(cfg.endpoint or ""),
        api_key=cfg.api_key,
        api_version=AZURE_OPENAI_API_VERSION,
    )


def chat_completion(
    client: Any,
    *,
    model: str,
    messages: list,
    temperature: float = 0.7,
    max_completion_tokens: int = 2048,
) -> str:
    """Run a chat completion and return the assistant text."""
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_completion_tokens=max_completion_tokens,
    )
    return response.choices[0].message.content
