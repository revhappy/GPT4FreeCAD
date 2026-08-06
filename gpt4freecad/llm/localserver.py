"""Ollama / LM Studio adapter - use the models you have already pulled.

Distinct from the ``machine`` provider, which downloads and supervises a
llama-server itself. This one attaches to a server you are already running, so
every model in your Ollama or LM Studio library is usable without GPT4FreeCAD
managing weights, ports or processes at all. Both apps expose an
OpenAI-compatible ``/v1`` surface, so one adapter covers them.

No API key, nothing leaves the machine.
"""

from __future__ import annotations

from typing import List, Optional

from .base import (
    ChatRequest, LLMError, ModelInfo, Provider, Reply, http_get_json,
    http_post_json, openai_reply, register,
)

# Where these servers listen out of the box. Probed in order.
KNOWN_SERVERS = (
    ("Ollama", "http://127.0.0.1:11434"),
    ("LM Studio", "http://127.0.0.1:1234"),
    ("Jan", "http://127.0.0.1:1337"),
)

_DEFAULT_BASE = KNOWN_SERVERS[0][1]


def _v1(base_url: str) -> str:
    """Normalise a base URL to its OpenAI-compatible /v1 root.

    People paste all of ``:11434``, ``:11434/``, ``:11434/v1`` and the full
    chat-completions URL; all four should work.
    """
    base = (base_url or _DEFAULT_BASE).strip().rstrip("/")
    for suffix in ("/chat/completions", "/v1"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
    return f"{base}/v1"


def probe(base_url: str, timeout: int = 3) -> Optional[List[str]]:
    """Model ids a server reports, or None if nothing is listening there."""
    try:
        data = http_get_json(f"{_v1(base_url)}/models", timeout=timeout)
    except LLMError:
        return None
    return [e["id"] for e in data.get("data", []) if e.get("id")]


def discover_servers(timeout: int = 2) -> List[tuple]:
    """``[(label, base_url, [model ids]), ...]`` for every server responding.

    Cheap enough to run when Settings opens: three connections to loopback that
    fail instantly when nothing is listening.
    """
    found = []
    for label, url in KNOWN_SERVERS:
        models = probe(url, timeout=timeout)
        if models is not None:
            found.append((label, url, models))
    return found


@register
class LocalServerProvider(Provider):
    id = "localserver"
    label = "Ollama / LM Studio"
    api_key_url = "https://ollama.com/download"
    requires_key = False
    default_models: List[str] = []   # there is no sensible default; ask the server
    can_list_models = True
    # Set from config at runtime, like the OpenAI endpoint override.
    base_url = _DEFAULT_BASE

    def fetch_models(self, api_key: str = "") -> List[ModelInfo]:
        models = probe(self.base_url, timeout=10)
        if models is None:
            raise LLMError(
                f"No server responding at {self.base_url}. Start Ollama "
                "(`ollama serve`) or LM Studio's local server, or point the "
                "Server URL at wherever yours is listening."
            )
        if not models:
            raise LLMError(
                f"The server at {self.base_url} is running but has no models. "
                "Pull one first (for example `ollama pull qwen3-coder`)."
            )
        return [ModelInfo(id=m, name=m) for m in sorted(models)]

    def chat(self, request: ChatRequest, api_key: str = "") -> Reply:
        payload = {
            "model": request.model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            data = http_post_json(
                f"{_v1(self.base_url)}/chat/completions", payload,
                # Local generation on CPU is slow; a cloud-sized timeout would
                # abandon a reply that was going to arrive.
                timeout=max(request.timeout, 600),
            )
        except LLMError as exc:
            if getattr(exc, "transient", False):
                raise LLMError(
                    f"Could not reach {self.base_url}. Is Ollama or LM Studio "
                    f"still running?\n\n{exc}"
                ) from exc
            raise
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"{self.label} returned no choices.")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not content:
            finish = choices[0].get("finish_reason")
            raise LLMError(
                f"{self.label} returned an empty message (finish_reason={finish})."
            )
        return openai_reply(message, content, data.get("usage"), self.label)
