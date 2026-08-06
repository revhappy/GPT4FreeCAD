"""Google Gemini (Generative Language API) adapter."""

from __future__ import annotations

from typing import List

from .base import (
    ChatRequest, LLMError, ModelInfo, Provider, http_get_json, http_post_json,
    register,
)

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Gemini 3 models use dynamic "thinking" and Google strongly recommends leaving
# temperature at its default (1.0); a lower value can cause looping / degraded
# output. They also reason before answering, so we give a generous output token
# floor to avoid empty MAX_TOKENS responses on small budgets.
_GEMINI3_MIN_OUTPUT_TOKENS = 8192

# Thinking levels supported by the Gemini 3 family (see the Gemini 3 dev guide).
THINKING_LEVELS = ("minimal", "low", "medium", "high")


def _is_gemini3(model: str) -> bool:
    return model.split("/")[-1].startswith("gemini-3")


def _resolve_thinking_level(model: str, level):
    """Return a valid thinkingLevel for ``model``, or None to omit it.

    'minimal' is not supported by Gemini 3 Pro, so it is bumped to 'low'.
    """
    if not level:
        return None
    level = str(level).strip().lower()
    if level not in THINKING_LEVELS:
        return None  # "default"/unknown -> let the model decide
    if level == "minimal" and "pro" in model:
        return "low"  # Pro does not support 'minimal'
    return level


@register
class GeminiProvider(Provider):
    id = "gemini"
    label = "Google Gemini"
    api_key_url = "https://aistudio.google.com/app/apikey"
    # Model IDs are editable in Settings; these are sensible current defaults.
    default_models = [
        "gemini-3.5-flash",         # stable frontier Flash (recommended default)
        "gemini-3-flash-preview",   # Gemini 3 Flash (preview)
        "gemini-3.1-pro-preview",   # Gemini 3.1 Pro (preview)
    ]
    can_list_models = True

    def fetch_models(self, api_key: str = "") -> List[ModelInfo]:
        if not api_key:
            raise LLMError("A Gemini API key is needed to list models.")
        data = http_get_json(f"{_BASE}?key={api_key}&pageSize=200")
        out = []
        for entry in data.get("models", []):
            name = (entry.get("name") or "").split("/")[-1]
            # The same endpoint lists embedding and image models, which have no
            # generateContent method - the only reliable way to tell them apart.
            if not name or "generateContent" not in (
                    entry.get("supportedGenerationMethods") or []):
                continue
            out.append(ModelInfo(
                id=name,
                name=entry.get("displayName") or name,
                context=int(entry.get("inputTokenLimit") or 0),
            ))
        return out

    def chat(self, request: ChatRequest, api_key: str) -> str:
        if not api_key:
            raise LLMError("No Google Gemini API key set. Open Settings to add one.")

        system_text, messages = request.split_system()

        contents = []
        for msg in messages:
            role = "model" if msg.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})

        model = request.model.split("/")[-1]  # tolerate a "models/" prefix

        generation_config = {}
        timeout = request.timeout
        if _is_gemini3(model):
            # Reasoning before answering can outlast the default 120 s timeout
            # at higher thinking levels (the Anthropic and local adapters make
            # the same allowance).
            timeout = max(timeout, 300)
            # Do NOT send temperature (let it default to 1.0 per Google's docs).
            generation_config["maxOutputTokens"] = max(
                request.max_tokens, _GEMINI3_MIN_OUTPUT_TOKENS
            )
            level = _resolve_thinking_level(model, request.thinking_level)
            if level:
                generation_config["thinkingConfig"] = {"thinkingLevel": level}
        else:
            generation_config["temperature"] = request.temperature
            generation_config["maxOutputTokens"] = request.max_tokens
        if request.json_mode:
            generation_config["responseMimeType"] = "application/json"

        payload = {
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}

        url = f"{_BASE}/{model}:generateContent?key={api_key}"
        data = http_post_json(url, payload, timeout=timeout)
        return _extract_text(data)


def _extract_text(data: dict) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback", {})
        block = feedback.get("blockReason")
        if block:
            raise LLMError(f"Gemini blocked the request ({block}). Try rephrasing.")
        raise LLMError("Gemini returned no candidates.")

    cand = candidates[0]
    finish = cand.get("finishReason")
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    if not text:
        if finish == "MAX_TOKENS":
            raise LLMError(
                "Gemini hit the output token limit before producing text. "
                "Increase 'Max tokens' in Settings."
            )
        raise LLMError(f"Gemini returned an empty response (finishReason={finish}).")
    return text
