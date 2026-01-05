"""
OpenAI provider implementation for GPT-CAD.
Supports GPT-4, GPT-4 Turbo, GPT-3.5 Turbo, and other OpenAI models.
"""

import json
import requests
from typing import List, Dict, Optional

from .base import AIProvider


class OpenAIProvider(AIProvider):
    """OpenAI API provider implementation."""

    name = "openai"
    display_name = "OpenAI"

    API_ENDPOINT = "https://api.openai.com/v1/chat/completions"

    # Available models (as of 2024)
    MODELS = [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-4",
        "gpt-3.5-turbo",
    ]

    DEFAULT_MODEL = "gpt-4o"

    def __init__(self):
        super().__init__()
        self._timeout = 120  # seconds

    def generate_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None
    ) -> str:
        """Generate a completion using OpenAI's API."""
        if not self._api_key:
            raise ValueError("API key not set. Please configure your OpenAI API key.")

        model = model or self.DEFAULT_MODEL

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        data = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        if max_tokens is not None:
            data["max_tokens"] = max_tokens

        response = requests.post(
            self.API_ENDPOINT,
            headers=headers,
            data=json.dumps(data),
            timeout=self._timeout
        )

        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        elif response.status_code == 401:
            raise ValueError("Invalid API key. Please check your OpenAI API key.")
        elif response.status_code == 429:
            raise Exception("Rate limit exceeded. Please wait and try again.")
        else:
            raise Exception(f"OpenAI API error {response.status_code}: {response.text}")

    def get_available_models(self) -> List[str]:
        """Return list of available OpenAI models."""
        return self.MODELS.copy()

    def get_default_model(self) -> str:
        """Return the default OpenAI model."""
        return self.DEFAULT_MODEL

    def validate_api_key(self, api_key: str) -> bool:
        """Validate an OpenAI API key by making a test request."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        # Use a minimal request to validate the key
        data = {
            "model": "gpt-3.5-turbo",
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
