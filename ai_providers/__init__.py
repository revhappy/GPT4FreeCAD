"""
AI Providers package for GPT-CAD.
Supports multiple AI platforms including OpenAI, Anthropic Claude, and more.
"""

from .base import AIProvider
from .openai_provider import OpenAIProvider
from .claude_provider import ClaudeProvider

# Provider registry for easy lookup
PROVIDERS = {
    "openai": OpenAIProvider,
    "claude": ClaudeProvider,
}

def get_provider(provider_name: str) -> AIProvider:
    """Get a provider instance by name."""
    if provider_name not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider_name}. Available: {list(PROVIDERS.keys())}")
    return PROVIDERS[provider_name]()

def list_providers() -> list:
    """Return list of available provider names."""
    return list(PROVIDERS.keys())
