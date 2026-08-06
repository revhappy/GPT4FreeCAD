"""OpenAI Chat Completions adapter.

Also works with any OpenAI-compatible endpoint (Azure OpenAI, OpenRouter, local
servers) by overriding the base URL in Settings.
"""

from __future__ import annotations

from typing import List

from .base import (
    ChatRequest, LLMError, ModelInfo, Provider, Reply, http_get_json,
    http_post_json, openai_reply, register,
)

_DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions"

# /v1/models lists everything on the account, most of which cannot hold a
# conversation. There is no "modality" field to filter on, so the exclusions are
# by name - wrong only if OpenAI ships a chat model named after a speech codec.
_NOT_CHAT = ("embed", "tts", "whisper", "dall-e", "moderation", "audio",
             "image", "realtime", "transcribe", "search", "babbage", "davinci",
             "sora", "guard")

# Reasoning models (gpt-5.x, o-series) differ from the gpt-4 family in three
# ways this adapter must honour: they take `max_completion_tokens` instead of
# `max_tokens` (which they reject with HTTP 400), they reject a non-default
# `temperature`, and their reasoning tokens are spent from the same output
# budget as the visible reply - so a 4096 cap can produce an empty message.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_MIN_REASONING_TOKENS = 16384


def _is_reasoning(model: str) -> bool:
    return (model or "").lower().startswith(_REASONING_PREFIXES)


@register
class OpenAIProvider(Provider):
    id = "openai"
    label = "OpenAI"
    api_key_url = "https://platform.openai.com/api-keys"
    default_models = [
        "gpt-5.1",
        "gpt-5",
        "gpt-5-mini",
        "gpt-4.1",
        "gpt-4o",
    ]
    # Optional override for OpenAI-compatible gateways; set by config at runtime.
    endpoint = _DEFAULT_ENDPOINT
    can_list_models = True

    def models_url(self) -> str:
        """/v1/models alongside whatever chat endpoint is configured."""
        base = (self.endpoint or _DEFAULT_ENDPOINT).split("/chat/completions")[0]
        return f"{base.rstrip('/')}/models"

    def fetch_models(self, api_key: str = "") -> List[ModelInfo]:
        if not api_key:
            raise LLMError("An OpenAI API key is needed to list models.")
        data = http_get_json(self.models_url(),
                             headers={"Authorization": f"Bearer {api_key}"})
        entries = [e for e in data.get("data", []) if e.get("id")]
        # Newest first: the reason for asking at all is to find what just shipped.
        entries.sort(key=lambda e: e.get("created") or 0, reverse=True)
        return [ModelInfo(id=e["id"], name=e["id"])
                for e in entries
                if not any(word in e["id"].lower() for word in _NOT_CHAT)]

    def chat(self, request: ChatRequest, api_key: str) -> str:
        if not api_key:
            raise LLMError("No OpenAI API key set. Open Settings to add one.")

        payload = {
            "model": request.model,
            "messages": request.messages,
        }
        timeout = request.timeout
        if _is_reasoning(request.model):
            payload["max_completion_tokens"] = max(
                request.max_tokens, _MIN_REASONING_TOKENS)
            # Reasoning before answering can outlast a cloud-sized timeout.
            timeout = max(timeout, 300)
        else:
            payload["max_tokens"] = request.max_tokens
            payload["temperature"] = request.temperature
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {api_key}"}
        data = http_post_json(self.endpoint, payload, headers=headers, timeout=timeout)
        return _extract_text(data)


def _extract_text(data: dict) -> Reply:
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("OpenAI returned no choices.")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        finish = choices[0].get("finish_reason")
        raise LLMError(f"OpenAI returned an empty message (finish_reason={finish}).")
    # Chat Completions never returns the reasoning text for the o-series and
    # gpt-5 models - only how many tokens it took. The panel reports the count
    # and says so, rather than showing an empty trace.
    return openai_reply(message, content, data.get("usage"), "OpenAI")
