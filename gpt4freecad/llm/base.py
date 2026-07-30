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
from typing import Dict, List, Optional


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

    @property
    def default_model(self) -> str:
        return self.default_models[0] if self.default_models else ""

    def chat(self, request: ChatRequest, api_key: str) -> str:
        """Run a chat completion and return the assistant text content."""
        raise NotImplementedError


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
