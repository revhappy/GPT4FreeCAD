"""xAI Grok adapter.

xAI serves an OpenAI-compatible API at api.x.ai, so the wire format is the
familiar one. It is a separate provider rather than an endpoint override so it
gets its own key, its own saved model and its own catalogue - the same reasons
OpenRouter is separate.

``default_models`` is deliberately thin. Model names here change often, and a
stale literal is worse than an empty box: use Browse… to pull the current list
from ``/v1/models`` and pick from what actually exists on the account.
"""

from __future__ import annotations

from typing import List

from .base import (
    ChatRequest, LLMError, ModelInfo, Provider, http_get_json, http_post_json,
    register,
)

_ENDPOINT = "https://api.x.ai/v1/chat/completions"
_MODELS_ENDPOINT = "https://api.x.ai/v1/models"

# The same account also exposes image models, which cannot return a program.
_NOT_CHAT = ("image", "embed", "vision-beta")

# Grok's reasoning models spend thinking tokens from the reply budget, the way
# Claude 5, Gemini 3 and gpt-5.x do, so a 4096 cap can return nothing at all.
_REASONING_HINTS = ("mini", "reason", "think")
_MIN_REASONING_TOKENS = 16384


def _is_reasoning(model: str) -> bool:
    return any(hint in (model or "").lower() for hint in _REASONING_HINTS)


@register
class GrokProvider(Provider):
    id = "grok"
    label = "xAI (Grok)"
    api_key_url = "https://console.x.ai/"
    default_models = ["grok-4", "grok-3", "grok-3-mini"]
    can_list_models = True

    def fetch_models(self, api_key: str = "") -> List[ModelInfo]:
        if not api_key:
            raise LLMError("An xAI API key is needed to list models.")
        data = http_get_json(_MODELS_ENDPOINT,
                             headers={"Authorization": f"Bearer {api_key}"})
        entries = [e for e in data.get("data", []) if e.get("id")]
        entries.sort(key=lambda e: e.get("created") or 0, reverse=True)
        return [ModelInfo(id=e["id"], name=e["id"])
                for e in entries
                if not any(word in e["id"].lower() for word in _NOT_CHAT)]

    def chat(self, request: ChatRequest, api_key: str) -> str:
        if not api_key:
            raise LLMError("No xAI API key set. Open Settings to add one.")

        payload = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
        }
        timeout = request.timeout
        if _is_reasoning(request.model):
            payload["max_tokens"] = max(request.max_tokens, _MIN_REASONING_TOKENS)
            timeout = max(timeout, 300)
        else:
            payload["max_tokens"] = request.max_tokens
        if request.json_mode:
            # xAI supports OpenAI-style structured outputs; the strict schema
            # is the one flavour it accepts. Falls back to plain JSON mode when
            # the engine did not supply one.
            if request.json_schema_strict:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "cad_program",
                        "strict": True,
                        "schema": request.json_schema_strict,
                    },
                }
            else:
                payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {api_key}"}
        data = http_post_json(_ENDPOINT, payload, headers=headers, timeout=timeout)
        return _extract_text(data)


def _extract_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("xAI returned no choices.")
    content = (choices[0].get("message") or {}).get("content")
    if not content:
        finish = choices[0].get("finish_reason")
        raise LLMError(f"xAI returned an empty message (finish_reason={finish}).")
    return content
