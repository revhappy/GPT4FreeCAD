"""Anthropic Claude (Messages API) adapter."""

from __future__ import annotations

from .base import ChatRequest, LLMError, Provider, http_post_json, register

_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"


@register
class AnthropicProvider(Provider):
    id = "anthropic"
    label = "Anthropic (Claude)"
    api_key_url = "https://console.anthropic.com/settings/keys"
    default_models = [
        "claude-sonnet-4-6",
        "claude-opus-4-8",
        "claude-haiku-4-5-20251001",
    ]

    def chat(self, request: ChatRequest, api_key: str) -> str:
        if not api_key:
            raise LLMError("No Anthropic API key set. Open Settings to add one.")

        system_text, messages = request.split_system()

        msgs = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages
        ]

        # JSON mode: prefill the assistant turn with "{" so the model is forced
        # to emit a JSON object. The prefill is NOT echoed back, so we re-add it.
        prefilled = False
        if request.json_mode:
            msgs.append({"role": "assistant", "content": "{"})
            prefilled = True

        payload = {
            "model": request.model,
            "messages": msgs,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if system_text:
            payload["system"] = system_text

        headers = {
            "x-api-key": api_key,
            "anthropic-version": _API_VERSION,
        }
        data = http_post_json(_ENDPOINT, payload, headers=headers, timeout=request.timeout)
        text = _extract_text(data)
        return ("{" + text) if prefilled else text


def _extract_text(data: dict) -> str:
    blocks = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    if not text:
        stop = data.get("stop_reason")
        raise LLMError(f"Claude returned no text (stop_reason={stop}).")
    return text
