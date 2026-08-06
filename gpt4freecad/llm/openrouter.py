"""OpenRouter adapter - one key, every open and closed model behind it.

OpenRouter speaks the OpenAI Chat Completions wire format, so the request side
is thin. What it adds is the catalogue: several hundred models from every major
lab plus the open-weight ecosystem, with context length, per-token pricing and
declared capabilities, served from a public endpoint that needs no key at all.

That catalogue is why this provider exists as its own entry rather than as an
endpoint override on the OpenAI one. It is also the answer to hard-coded model
lists going stale: measured on 2026-08-05 the catalogue held 339 models, most of
them released after any list this addon could ship with.
"""

from __future__ import annotations

from typing import List

from .base import (
    ChatRequest, LLMError, ModelInfo, Provider, http_get_json, http_post_json,
    price_per_million, register,
)

_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_MODELS_ENDPOINT = "https://openrouter.ai/api/v1/models"

# Sent so OpenRouter can attribute traffic; both are optional and neither
# carries anything about the user or the part being designed.
_REFERER = "https://github.com/RobbSharma/GPT4FreeCAD"
_TITLE = "GPT4FreeCAD"

# Structured mode needs the model to accept a response_format. 286 of the 339
# models advertise it; the rest would fail every generation, so the picker flags
# them rather than letting the user find out one wasted request later.
_JSON_PARAMS = ("response_format", "structured_outputs")


@register
class OpenRouterProvider(Provider):
    id = "openrouter"
    label = "OpenRouter"
    api_key_url = "https://openrouter.ai/keys"
    # A deliberately short starting point - the real list comes from Browse.
    # Chosen for structured output quality rather than raw benchmark scores.
    default_models = [
        "anthropic/claude-sonnet-5",
        "openai/gpt-5.1",
        "google/gemini-3.5-flash",
        "deepseek/deepseek-chat",
        "qwen/qwen3-coder",
    ]
    can_list_models = True

    def fetch_models(self, api_key: str = "") -> List[ModelInfo]:
        """The full catalogue. Works without a key - the endpoint is public."""
        data = http_get_json(_MODELS_ENDPOINT, headers={"User-Agent": _TITLE})
        out = []
        for entry in data.get("data", []):
            model_id = entry.get("id")
            if not model_id:
                continue
            # Skip models that cannot return text at all (image/audio output).
            modalities = (entry.get("architecture") or {}).get("output_modalities")
            if modalities and "text" not in modalities:
                continue
            pricing = entry.get("pricing") or {}
            price_in = price_per_million(pricing.get("prompt"))
            price_out = price_per_million(pricing.get("completion"))
            supported = entry.get("supported_parameters") or []
            out.append(ModelInfo(
                id=model_id,
                name=entry.get("name") or model_id,
                context=int(entry.get("context_length") or 0),
                price_in=price_in,
                price_out=price_out,
                json_mode=any(p in supported for p in _JSON_PARAMS),
                free=(price_in <= 0 and price_out <= 0),
            ))
        out.sort(key=lambda m: m.id)
        return out

    def chat(self, request: ChatRequest, api_key: str) -> str:
        if not api_key:
            raise LLMError("No OpenRouter API key set. Open Settings to add one.")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": _REFERER,
            "X-Title": _TITLE,
        }
        timeout = max(request.timeout, 180)
        strict = request.json_schema_strict
        schema_wanted = (request.json_mode and strict
                         and request.model not in _NO_SCHEMA)

        if schema_wanted:
            try:
                data = http_post_json(
                    _ENDPOINT, _payload(request, strict),
                    headers=headers, timeout=timeout)
                return _extract_text(data)
            except LLMError as exc:
                if not _is_schema_rejection(exc):
                    raise
                # Capability is advertised per model and not every backing
                # provider honours it. Downgrade once, remember, carry on -
                # better than failing a generation over a response_format.
                _NO_SCHEMA.add(request.model)

        data = http_post_json(_ENDPOINT, _payload(request, None),
                              headers=headers, timeout=timeout)
        return _extract_text(data)


# Models that turned out not to accept a json_schema after all. Session-scoped:
# the downgrade costs one request the first time and nothing afterwards.
_NO_SCHEMA: set = set()


def _payload(request: ChatRequest, json_schema) -> dict:
    payload = {
        "model": request.model,
        "messages": request.messages,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
    }
    if json_schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "cad_program",
                "strict": True,
                "schema": json_schema,
            },
        }
    elif request.json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _is_schema_rejection(exc: LLMError) -> bool:
    """True when the failure is about response_format rather than the request.

    A model that cannot do structured output says so in a 400; a bad key, a
    dead network or an out-of-credit account must not be retried as if the
    schema were the problem.
    """
    if getattr(exc, "transient", False):
        return False
    text = str(exc).lower()
    if not text.startswith("http 4"):
        return False
    return any(word in text for word in (
        "response_format", "json_schema", "schema", "structured output",
        "does not support"))


def _extract_text(data: dict) -> str:
    """Pull the reply out, turning OpenRouter's own error shapes into LLMError.

    Unlike OpenAI, a routing failure can come back as HTTP 200 with an "error"
    member, because the request reached OpenRouter even though it never reached
    a model. Without this the panel would report "no choices" for what is
    really "that model is down" or "you are out of credit".
    """
    error = data.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise LLMError(f"OpenRouter: {message}")
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("OpenRouter returned no choices.")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        finish = choices[0].get("finish_reason")
        raise LLMError(
            f"OpenRouter returned an empty message (finish_reason={finish}). "
            "Some models ignore a JSON-mode request; try another model."
        )
    return content
