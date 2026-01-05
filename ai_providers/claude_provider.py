"""
Anthropic Claude provider implementation for GPT-CAD.
Supports Claude 3.5 Sonnet, Claude 3 Opus, and other Claude models.
"""

import json
import requests
from typing import List, Dict, Optional, Any

from .base import AIProvider


class ClaudeProvider(AIProvider):
    """Anthropic Claude API provider implementation."""

    name = "claude"
    display_name = "Anthropic Claude"

    API_ENDPOINT = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    # Available models
    MODELS = [
        "claude-sonnet-4-20250514",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
        "claude-3-haiku-20240307",
    ]

    DEFAULT_MODEL = "claude-sonnet-4-20250514"

    def __init__(self):
        super().__init__()
        self._timeout = 120  # seconds

    def convert_messages(self, messages: List[Dict[str, str]]) -> tuple:
        """
        Convert OpenAI-style messages to Claude format.
        Claude uses a separate system parameter and different message structure.

        Returns:
            Tuple of (system_prompt, messages_list)
        """
        system_prompt = None
        converted_messages = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                # Claude takes system as a separate parameter
                system_prompt = content
            elif role == "assistant":
                converted_messages.append({
                    "role": "assistant",
                    "content": content
                })
            else:
                # user role
                converted_messages.append({
                    "role": "user",
                    "content": content
                })

        return system_prompt, converted_messages

    def generate_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None
    ) -> str:
        """Generate a completion using Anthropic's Claude API."""
        if not self._api_key:
            raise ValueError("API key not set. Please configure your Anthropic API key.")

        model = model or self.DEFAULT_MODEL
        max_tokens = max_tokens or 4096  # Claude requires max_tokens

        # Convert messages to Claude format
        system_prompt, claude_messages = self.convert_messages(messages)

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": self.API_VERSION,
        }

        data = {
            "model": model,
            "messages": claude_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if system_prompt:
            data["system"] = system_prompt

        response = requests.post(
            self.API_ENDPOINT,
            headers=headers,
            data=json.dumps(data),
            timeout=self._timeout
        )

        if response.status_code == 200:
            response_data = response.json()
            # Claude returns content as a list of content blocks
            content_blocks = response_data.get("content", [])
            if content_blocks and len(content_blocks) > 0:
                return content_blocks[0].get("text", "")
            return ""
        elif response.status_code == 401:
            raise ValueError("Invalid API key. Please check your Anthropic API key.")
        elif response.status_code == 429:
            raise Exception("Rate limit exceeded. Please wait and try again.")
        else:
            raise Exception(f"Claude API error {response.status_code}: {response.text}")

    def get_available_models(self) -> List[str]:
        """Return list of available Claude models."""
        return self.MODELS.copy()

    def get_default_model(self) -> str:
        """Return the default Claude model."""
        return self.DEFAULT_MODEL

    def validate_api_key(self, api_key: str) -> bool:
        """Validate an Anthropic API key by making a test request."""
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": self.API_VERSION,
        }

        # Use a minimal request to validate the key
        data = {
            "model": "claude-3-haiku-20240307",
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 1,
        }

        try:
            response = requests.post(
                self.API_ENDPOINT,
                headers=headers,
                data=json.dumps(data),
                timeout=10
            )
            return response.status_code == 200
        except Exception:
            return False
