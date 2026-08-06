"""Provider abstraction + shared helpers (stdlib only, no FreeCAD).

Everything here works on a vanilla CPython interpreter so the provider layer can
be unit-tested without FreeCAD installed.
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class LLMError(Exception):
    """Base class for all provider errors (network, HTTP, parsing)."""


class AuthError(LLMError):
    """Authentication failed - usually a missing or invalid API key."""


class RateLimitError(LLMError):
    """The provider returned a 429 / quota error."""


# --------------------------------------------------------------------------- #
# Request value object
# --------------------------------------------------------------------------- #
@dataclass
class ChatRequest:
    """A provider-agnostic chat request.

    ``messages`` use the canonical OpenAI shape: a list of
    ``{"role": "system"|"user"|"assistant", "content": str}``. Each provider
    adapter translates this into its native wire format.
    """

    messages: List[Dict[str, str]]
    model: str
    temperature: float = 0.2
    max_tokens: int = 4096
    json_mode: bool = False
    timeout: int = 120
    # Gemini 3 only: "minimal" | "low" | "medium" | "high". Anything else (incl.
    # None / "default") means "let the model decide". Ignored by other providers.
    thinking_level: Optional[str] = None
    # Optional JSON schema for the expected reply, used with json_mode. Local
    # models enforce it as a grammar (so malformed output is impossible); cloud
    # providers that have no equivalent simply ignore it.
    json_schema: Optional[Dict[str, Any]] = None
    # The same schema in the dialect OpenAI-style structured outputs require
    # (closed objects, every property required, nullable instead of optional).
    # Carried separately because the two are not interchangeable: the grammar
    # path wants the precise one, and strict mode refuses it. Providers take
    # whichever they can enforce, so the llm layer stays free of any CAD import.
    json_schema_strict: Optional[Dict[str, Any]] = None

    def split_system(self) -> "tuple[str, List[Dict[str, str]]]":
        """Return ``(system_text, non_system_messages)``.

        Used by providers (Anthropic, Gemini) that carry the system prompt in a
        dedicated field instead of inline in the message list.
        """
        system_parts = [m["content"] for m in self.messages if m.get("role") == "system"]
        rest = [m for m in self.messages if m.get("role") != "system"]
        return "\n\n".join(system_parts), rest


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #
# Transient failures (rate limits, server hiccups, network blips) are retried
# with these delays before giving up; auth and other client errors never are.
_RETRY_DELAYS = (1.0, 3.0)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}


def http_post_json(
    url: str,
    payload: dict,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 120,
) -> dict:
    """POST ``payload`` as JSON and return the parsed JSON response.

    Transient failures (429 / 5xx / network errors) are retried with a short
    backoff. Raises :class:`AuthError` (401/403), :class:`RateLimitError` (429)
    or :class:`LLMError` for other failures, always including the server's body
    so the user sees a useful message in the panel.
    """
    body = json.dumps(payload).encode("utf-8")
    final_headers = {"Content-Type": "application/json"}
    if headers:
        final_headers.update(headers)

    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return _post_once(url, body, final_headers, timeout)
        except LLMError as exc:
            transient = isinstance(exc, RateLimitError) or getattr(exc, "transient", False)
            if not transient or attempt >= len(_RETRY_DELAYS):
                raise
            time.sleep(_RETRY_DELAYS[attempt])


def _post_once(url: str, body: bytes, headers: Dict[str, str], timeout: int) -> dict:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    # Some corporate Python builds ship without a usable cert bundle; fall back
    # to a default context (still verifies, just lets the OS pick the store).
    context = ssl.create_default_context()

    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = _read_error_body(exc)
        status = exc.code
        msg = f"HTTP {status}: {detail}"
        if status in (401, 403):
            raise AuthError(
                f"{msg}\n\nCheck that your API key is correct and has access to this model."
            ) from exc
        if status == 429:
            raise RateLimitError(
                f"{msg}\n\nRate limit or quota exceeded - wait a moment or check billing."
            ) from exc
        error = LLMError(msg)
        error.transient = status in _RETRYABLE_STATUS
        raise error from exc
    except urllib.error.URLError as exc:
        error = LLMError(
            f"Network error: {exc.reason}\n\n"
            "Check your internet connection / proxy and that the endpoint URL is reachable."
        )
        error.transient = True
        raise error from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"Could not parse provider response as JSON: {exc}") from exc


def http_get_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> dict:
    """GET ``url`` and return the parsed JSON response.

    Same error translation as :func:`http_post_json`, without the retries -
    every caller is a catalogue lookup that has a usable fallback, so a slow
    failure is worse than a fast one.
    """
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=ssl.create_default_context()
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = _read_error_body(exc)
        if exc.code in (401, 403):
            raise AuthError(f"HTTP {exc.code}: {detail}") from exc
        raise LLMError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        error = LLMError(f"Network error: {exc.reason}")
        error.transient = True
        raise error from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"Could not parse response as JSON: {exc}") from exc


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return str(exc)
    # Surface the provider's structured error message if there is one.
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            err = data.get("error", data)
            if isinstance(err, dict) and "message" in err:
                return str(err["message"])
            if isinstance(err, list) and err and isinstance(err[0], dict):
                return str(err[0].get("message", raw))
        return raw[:600]
    except Exception:
        return raw[:600]


# --------------------------------------------------------------------------- #
# Replies
# --------------------------------------------------------------------------- #
class Reply(str):
    """The assistant's text, with the metadata the panel shows alongside it.

    ``Provider.chat`` has always returned the reply text, and everything
    downstream - history, the editable preview, JSON extraction - treats it as a
    string. Reasoning traces and token counts are *about* that text rather than
    part of it, so they ride along as attributes of a ``str`` subclass: callers
    that only want the reply keep working untouched, and the panel reads
    ``reply.reasoning`` when it wants to show the model's thinking.

    The attributes survive only as long as the object does; ``reply[1:]`` or
    ``"".join(...)`` gives a plain ``str`` back. Read them at the call site,
    which is what :mod:`gpt4freecad.engine` does.
    """

    __slots__ = ("reasoning", "usage")

    def __new__(cls, text, reasoning: str = "", usage: Optional[dict] = None):
        obj = super().__new__(cls, text if text is not None else "")
        obj.reasoning = (reasoning or "").strip()
        obj.usage = dict(usage or {})
        return obj


def reasoning_of(reply: Any) -> str:
    """The reasoning attached to a reply, or ``""`` for a plain string."""
    return getattr(reply, "reasoning", "") or ""


def usage_of(reply: Any) -> dict:
    """The token usage attached to a reply, or ``{}`` for a plain string."""
    return dict(getattr(reply, "usage", None) or {})


# Local models and OpenAI-compatible servers frequently emit their reasoning
# inline, wrapped in <think>...</think>, instead of in a separate field.
_THINK_RE = re.compile(r"<(think|thinking|reasoning)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
# A trace that was cut off by the token limit never gets its closing tag.
_OPEN_THINK_RE = re.compile(r"<(think|thinking|reasoning)>(.*)$", re.DOTALL | re.IGNORECASE)


def split_think_tags(text: str) -> "tuple[str, str]":
    """Split inline ``<think>`` reasoning out of a reply. Returns ``(text, reasoning)``.

    Pulling the trace out is not only for display: left in place it defeats the
    "reply is a JSON object" straight parse, so every local model paid for a
    fallback path it did not need.
    """
    if not text:
        return "", ""
    traces = [match.group(2).strip() for match in _THINK_RE.finditer(text)]
    remainder = _THINK_RE.sub("", text)
    unterminated = _OPEN_THINK_RE.search(remainder)
    if unterminated is not None:
        traces.append(unterminated.group(2).strip())
        remainder = remainder[: unterminated.start()]
    return remainder.strip(), "\n\n".join(t for t in traces if t).strip()


def openai_reply(message: dict, content: str, usage: Any = None,
                 label: str = "The model") -> "Reply":
    """Build a :class:`Reply` from an OpenAI-shaped assistant message.

    Four adapters speak this dialect, and each server puts reasoning somewhere
    slightly different: a ``reasoning_content`` field (LM Studio, vLLM), a
    ``reasoning`` field (OpenRouter), or inline ``<think>`` tags in the content
    itself (llama.cpp and most GGUF chat templates). All three are handled here
    so no adapter has to care which one it is talking to.
    """
    text, inline = split_think_tags(content)
    reported = message.get("reasoning_content") or message.get("reasoning") or ""
    if not isinstance(reported, str):
        # OpenRouter can send a structured reasoning_details list instead.
        reported = ""
    reasoning = "\n\n".join(p for p in (reported.strip(), inline) if p)
    if not text:
        raise LLMError(
            f"{label} returned reasoning but no answer. Raise 'Max tokens' in "
            "Settings - the thinking used the whole budget."
        )
    return Reply(text, reasoning=reasoning, usage=openai_usage(usage))


def openai_usage(raw: Any) -> dict:
    """Token counts from an OpenAI-shaped ``usage`` object."""
    if not isinstance(raw, dict):
        return {}
    usage = {"input": raw.get("prompt_tokens", 0),
             "output": raw.get("completion_tokens", 0)}
    details = raw.get("completion_tokens_details")
    if isinstance(details, dict) and details.get("reasoning_tokens"):
        usage["reasoning"] = details["reasoning_tokens"]
    return usage


# --------------------------------------------------------------------------- #
# Tolerant JSON extraction
# --------------------------------------------------------------------------- #
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """Best-effort extraction of a JSON object from a model reply.

    Handles three common cases: a clean JSON body, a ```json ... ``` fenced
    block, or JSON embedded in prose. Raises :class:`LLMError` if nothing
    parseable is found.
    """
    if text is None:
        raise LLMError("Empty response from model.")
    text = text.strip()

    # 1. Straight parse.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Fenced code block(s) - try each.
    for match in _FENCE_RE.finditer(text):
        candidate = match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    # 3. First balanced {...} span.
    snippet = _first_balanced_object(text)
    if snippet is not None:
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            pass

    raise LLMError(
        "Model did not return valid JSON. Raw reply:\n\n" + text[:800]
    )


def _first_balanced_object(text: str) -> Optional[str]:
    """Return the first brace-balanced ``{...}`` substring, respecting strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# --------------------------------------------------------------------------- #
# Model catalogue
# --------------------------------------------------------------------------- #
@dataclass
class ModelInfo:
    """One model in a provider's live catalogue.

    Only ``id`` is guaranteed; a provider that reports nothing else still
    produces a usable entry. ``json_mode`` is the field that matters most here -
    structured mode is the whole point of this addon, and a model that cannot be
    asked for JSON will fail every time.
    """

    id: str
    name: str = ""
    context: int = 0
    price_in: float = 0.0      # USD per million input tokens
    price_out: float = 0.0     # USD per million output tokens
    json_mode: bool = True     # provider says it accepts a JSON/schema request
    free: bool = False

    @property
    def label(self) -> str:
        return self.name or self.id

    def matches(self, needle: str) -> bool:
        """Case-insensitive match over id and display name, for the picker."""
        needle = (needle or "").strip().lower()
        if not needle:
            return True
        return all(word in f"{self.id} {self.name}".lower()
                   for word in needle.split())


def price_per_million(raw: Any) -> float:
    """USD per million tokens from a provider's per-token price string.

    Providers quote per-token prices as strings ("0.00000125"), and use -1 to
    mean "variable". Anything unparseable becomes 0.0, which the picker shows
    as free rather than inventing a number.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value * 1_000_000 if value > 0 else 0.0


# --------------------------------------------------------------------------- #
# Provider base + registry
# --------------------------------------------------------------------------- #
class Provider:
    """Base class for an LLM provider adapter.

    Subclasses set the class attributes and implement :meth:`chat`.
    """

    id: str = ""
    label: str = ""
    api_key_url: str = ""
    default_models: List[str] = []
    requires_key: bool = True
    # True when fetch_models() can reach a live catalogue. Providers whose
    # models only ever come from default_models leave this False so the UI
    # does not offer a Browse button that can only disappoint.
    can_list_models: bool = False

    @property
    def default_model(self) -> str:
        return self.default_models[0] if self.default_models else ""

    def chat(self, request: ChatRequest, api_key: str) -> "Reply":
        """Run a chat completion and return the assistant text content.

        The return value is a :class:`Reply` - a ``str`` carrying the reasoning
        trace and token usage when the provider reports them.
        """
        raise NotImplementedError

    def fetch_models(self, api_key: str = "") -> List[ModelInfo]:
        """The provider's live model catalogue.

        Hard-coded lists go stale the moment a provider ships something, which
        is exactly when you want to use it. Every adapter that can ask its
        provider what exists does so; :attr:`default_models` is the offline
        fallback, not the source of truth. Raises :class:`LLMError` on failure
        so the caller can tell "nothing available" from "could not reach it".
        """
        raise LLMError(f"{self.label} has no model catalogue to list.")


_REGISTRY: "dict[str, Provider]" = {}


def register(cls):
    """Class decorator that instantiates and registers a provider."""
    instance = cls()
    if not instance.id:
        raise ValueError(f"Provider {cls.__name__} must define an 'id'.")
    _REGISTRY[instance.id] = instance
    return cls


def get_provider(provider_id: str) -> Provider:
    try:
        return _REGISTRY[provider_id]
    except KeyError:
        raise LLMError(
            f"Unknown provider '{provider_id}'. Available: {', '.join(_REGISTRY)}"
        )


def all_providers() -> "List[Provider]":
    """Registered providers, ordered as registered."""
    return list(_REGISTRY.values())
