"""Anthropic Claude (Messages API) adapter."""

from __future__ import annotations

from typing import List

from .base import (
    ChatRequest, LLMError, ModelInfo, Provider, Reply, http_get_json,
    http_post_json, register,
)

_ENDPOINT = "https://api.anthropic.com/v1/messages"
_MODELS_ENDPOINT = "https://api.anthropic.com/v1/models?limit=100"
_API_VERSION = "2023-06-01"

# Claude 4.7+ / Claude 5 models reject sampling params (temperature/top_p/top_k)
# and last-turn assistant prefill with HTTP 400 - steering is prompt-only there.
_NO_SAMPLING_PREFIXES = (
    "claude-fable-5", "claude-mythos", "claude-opus-5",
    "claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5",
)

# Models that think by default. Thinking output shares the max_tokens budget
# with the visible reply, so a cloud-sized cap (the app default is 4096, the
# Settings test used 16) truncates the answer mid-plan or cuts it off before
# any text at all. Give these models a floor, like the Gemini adapter does.
_THINKING_DEFAULT_PREFIXES = (
    "claude-fable-5", "claude-mythos", "claude-opus-5", "claude-sonnet-5",
)
_MIN_THINKING_MAX_TOKENS = 16384

# Models whose safety classifiers can decline a request (stop_reason "refusal").
# The server-side fallback beta reruns the same request on another Claude model
# inside the same call instead of returning the refusal.
_FALLBACK_PREFIXES = ("claude-fable-5", "claude-mythos", "claude-opus-5")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Models that take adaptive thinking. Older Claude models want a fixed
# budget_tokens instead, which is a different (and now deprecated) contract, so
# they are simply left alone - no thinking parameter, no trace to show.
_ADAPTIVE_THINKING_PREFIXES = (
    "claude-fable-5", "claude-mythos", "claude-opus-5", "claude-opus-4-8",
    "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-5", "claude-sonnet-4-6",
)


@register
class AnthropicProvider(Provider):
    id = "anthropic"
    label = "Anthropic (Claude)"
    api_key_url = "https://console.anthropic.com/settings/keys"
    default_models = [
        "claude-opus-5",
        "claude-fable-5",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ]
    can_list_models = True

    def fetch_models(self, api_key: str = "") -> List[ModelInfo]:
        if not api_key:
            raise LLMError("An Anthropic API key is needed to list models.")
        data = http_get_json(_MODELS_ENDPOINT, headers={
            "x-api-key": api_key,
            "anthropic-version": _API_VERSION,
        })
        # The API already returns newest first; every entry is a chat model.
        return [ModelInfo(id=e["id"], name=e.get("display_name") or e["id"])
                for e in data.get("data", []) if e.get("id")]

    def chat(self, request: ChatRequest, api_key: str) -> str:
        if not api_key:
            raise LLMError("No Anthropic API key set. Open Settings to add one.")

        system_text, messages = request.split_system()
        model = (request.model or "").lower()

        msgs = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages
        ]

        max_tokens = request.max_tokens
        if model.startswith(_THINKING_DEFAULT_PREFIXES):
            max_tokens = max(max_tokens, _MIN_THINKING_MAX_TOKENS)

        payload = {
            "model": request.model,
            "messages": msgs,
            "max_tokens": max_tokens,
        }
        if not model.startswith(_NO_SAMPLING_PREFIXES):
            payload["temperature"] = request.temperature
        if system_text:
            payload["system"] = system_text
        if model.startswith(_ADAPTIVE_THINKING_PREFIXES):
            # These models think either way; "summarized" is what makes the
            # reasoning readable instead of an empty block. Visibility is all it
            # changes - the thinking happens, and is billed, regardless.
            payload["thinking"] = {"type": "adaptive", "display": "summarized"}

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
            # Both extras below are best-effort: a key without the fallback beta,
            # or a model that turns out not to take adaptive thinking, should
            # cost the request its garnish - not the whole generation.
            message = str(exc).lower()
            retry = False
            if "fallbacks" in payload and "fallback" in message:
                payload.pop("fallbacks", None)
                headers.pop("anthropic-beta", None)
                retry = True
            if "thinking" in payload and "thinking" in message:
                payload.pop("thinking", None)
                retry = True
            if not retry:
                raise
            data = http_post_json(_ENDPOINT, payload, headers=headers, timeout=timeout)
        return _extract_reply(data)


def _extract_reply(data: dict) -> Reply:
    stop = data.get("stop_reason")
    blocks = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    # Thinking blocks are present whenever the model thought; their text is
    # empty unless the request asked for it to be summarized.
    reasoning = "\n\n".join(
        b.get("thinking", "") for b in blocks if b.get("type") == "thinking")
    if not text:
        if stop == "refusal":
            raise LLMError(
                "Claude declined this request (safety classifiers). "
                "Rephrase the description and try again."
            )
        raise LLMError(f"Claude returned no text (stop_reason={stop}).")
    return Reply(text, reasoning=reasoning, usage=_usage(data.get("usage")))


def _usage(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    usage = {"input": raw.get("input_tokens", 0),
             "output": raw.get("output_tokens", 0)}
    cached = raw.get("cache_read_input_tokens") or 0
    if cached:
        usage["cached"] = cached
    return usage
