"""
Legacy GPT-4 integration module.
DEPRECATED: Use ai_providers module instead.

This module is kept for backwards compatibility.
"""

import warnings
from ai_providers import get_provider
from config import get_config, ask_for_api_key

# Show deprecation warning when imported
warnings.warn(
    "gpt4_integration is deprecated. Use ai_providers module instead.",
    DeprecationWarning,
    stacklevel=2
)


def generate_chat_completion(messages, model="gpt-4", temperature=0.3, max_tokens=None):
    """
    Legacy function for generating chat completions.
    Redirects to new provider system.

    DEPRECATED: Use ai_providers.get_provider('openai').generate_completion() instead.
    """
    config = get_config()
    provider = get_provider("openai")

    # Get API key
    api_key = config.get_api_key("openai")
    if not api_key:
        api_key = ask_for_api_key("openai", "OpenAI")
        if not api_key:
            raise ValueError("API key not provided for OpenAI")

    provider.api_key = api_key

    return provider.generate_completion(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens
    )
