"""OpenAI Chat Completions adapter.

Also works with any OpenAI-compatible endpoint (Azure OpenAI, OpenRouter, local
servers) by overriding the base URL in Settings.
"""

from __future__ import annotations

from .base import ChatRequest, LLMError, Provider, http_post_json, register

_DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions"


@register
class OpenAIProvider(Provider):
    id = "openai"
    label = "OpenAI"
    api_key_url = "https://platform.openai.com/api-keys"
    default_models = [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
    ]
    # Optional override for OpenAI-compatible gateways; set by config at runtime.
    endpoint = _DEFAULT_ENDPOINT

    def chat(self, request: ChatRequest, api_key: str) -> str:
        if not api_key:
            raise LLMError("No OpenAI API key set. Open Settings to add one.")

        payload = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {api_key}"}
        data = http_post_json(self.endpoint, payload, headers=headers, timeout=request.timeout)
        return _extract_text(data)


def _extract_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("OpenAI returned no choices.")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        finish = choices[0].get("finish_reason")
        raise LLMError(f"OpenAI returned an empty message (finish_reason={finish}).")
    return content
