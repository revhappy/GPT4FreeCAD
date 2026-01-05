"""
Abstract base class for AI providers.
All AI provider implementations must inherit from this class.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any


class AIProvider(ABC):
    """Abstract base class for AI providers."""

    # Provider identification
    name: str = "base"
    display_name: str = "Base Provider"

    def __init__(self):
        self._api_key: Optional[str] = None

    @property
    def api_key(self) -> Optional[str]:
        """Get the current API key."""
        return self._api_key

    @api_key.setter
    def api_key(self, value: str):
        """Set the API key."""
        self._api_key = value

    @abstractmethod
    def generate_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Generate a completion from the AI model.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            model: Model identifier (uses default if None)
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens in response

        Returns:
            The generated text response
        """
        pass

    @abstractmethod
    def get_available_models(self) -> List[str]:
        """
        Return list of available models for this provider.

        Returns:
            List of model identifiers
        """
        pass

    @abstractmethod
    def get_default_model(self) -> str:
        """
        Return the default model for this provider.

        Returns:
            Default model identifier
        """
        pass

    @abstractmethod
    def validate_api_key(self, api_key: str) -> bool:
        """
        Validate the API key for this provider.

        Args:
            api_key: The API key to validate

        Returns:
            True if valid, False otherwise
        """
        pass

    def convert_messages(self, messages: List[Dict[str, str]]) -> Any:
        """
        Convert messages to provider-specific format.
        Default implementation returns messages as-is (OpenAI format).
        Override in subclasses for different formats.

        Args:
            messages: Messages in OpenAI format

        Returns:
            Messages in provider-specific format
        """
        return messages

    def get_api_key_env_var(self) -> str:
        """
        Return the environment variable name for this provider's API key.

        Returns:
            Environment variable name
        """
        return f"{self.name.upper()}_API_KEY"

    def get_api_key_file(self) -> str:
        """
        Return the filename for storing this provider's API key.

        Returns:
            Filename (without path)
        """
        return f".{self.name}_api_key.txt"
