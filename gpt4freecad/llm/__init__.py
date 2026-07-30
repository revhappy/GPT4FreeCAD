"""Multi-provider LLM layer.

The public surface is small and provider-agnostic::

    from gpt4freecad.llm import get_provider, all_providers, ChatRequest

    provider = get_provider("gemini")
    reply = provider.chat(ChatRequest(messages=[...], model="gemini-2.5-flash",
                                      json_mode=True), api_key="...")

Adding a provider is a matter of subclassing :class:`~gpt4freecad.llm.base.Provider`
and calling :func:`register`. Importing this package registers the built-ins.
"""

from .base import (
    ChatRequest,
    Provider,
    LLMError,
    AuthError,
    RateLimitError,
    extract_json,
    register,
    get_provider,
    all_providers,
)

# Importing the modules registers the providers via the @register decorator.
from . import openai as _openai  # noqa: F401
from . import anthropic as _anthropic  # noqa: F401
from . import gemini as _gemini  # noqa: F401
from . import local as _local  # noqa: F401

__all__ = [
    "ChatRequest",
    "Provider",
    "LLMError",
    "AuthError",
    "RateLimitError",
    "extract_json",
    "register",
    "get_provider",
    "all_providers",
]
