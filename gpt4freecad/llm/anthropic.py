"""Anthropic Claude (Messages API) adapter."""

from __future__ import annotations

from .base import ChatRequest, LLMError, Provider, http_post_json, register

_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"

# Claude 4.7+ / Claude 5 models reject sampling params (temperature/top_p/top_k)
# and last-turn assistant prefill with HTTP 400 - steering is prompt-only there.
_NO_SAMPLING_PREFIXES = (
    "claude-fable-5", "claude-mythos", "claude-opus-5",
    "claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5",
)

# Models whose safety classifiers can decline a request (stop_reason "refusal").
# The server-side fallback beta reruns the same request on another Claude model
# inside the same call instead of returning the refusal.
_FALLBACK_PREFIXES = ("claude-fable-5", "claude-mythos", "claude-opus-5")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


@register
class AnthropicProvider(Provider):
    id = "anthropic"
    label = "Anthropic (Claude)"
    api_key_url = "https://console.anthropic.com/settings/keys"
    default_models = [
        "claude-opus-5",
        "claude-fable-5",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ]

    def chat(self, request: ChatRequest, api_key: str) -> str:
        if not api_key:
            raise LLMError("No Anthropic API key set. Open Settings to add one.")

        system_text, messages = request.split_system()
        model = (request.model or "").lower()

        msgs = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages
        ]

        payload = {
            "model": request.model,
            "messages": msgs,
            "max_tokens": request.max_tokens,
        }
        if not model.startswith(_NO_SAMPLING_PREFIXES):
            payload["temperature"] = request.temperature
        if system_text:
            payload["system"] = system_text

        headers = {
            "x-api-key": api_key,
            "anthropic-version": _API_VERSION,
        }
        if model.startswith(_FALLBACK_PREFIXES):
            payload["fallbacks"] = "default"
            headers["anthropic-beta"] = _FALLBACK_BETA

        # Claude 5-generation models always think before answering; a hard task
        # can run past the default 120 s request timeout.
        timeout = max(request.timeout, 300)

        try:
            data = http_post_json(_ENDPOINT, payload, headers=headers, timeout=timeout)
        except LLMError as exc:
            # If the org's key doesn't have the fallback beta, retry without it.
            if "fallbacks" in payload and "fallback" in str(exc).lower():
                payload.pop("fallbacks", None)
                headers.pop("anthropic-beta", None)
                data = http_post_json(_ENDPOINT, payload, headers=headers, timeout=timeout)
            else:
                raise
        return _extract_text(data)


def _extract_text(data: dict) -> str:
    stop = data.get("stop_reason")
    blocks = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    if not text:
        if stop == "refusal":
            raise LLMError(
                "Claude declined this request (safety classifiers). "
                "Rephrase the description and try again."
            )
        raise LLMError(f"Claude returned no text (stop_reason={stop}).")
    return text
