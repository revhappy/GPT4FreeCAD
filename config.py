"""
Configuration management for GPT-CAD.
Handles provider settings, API keys, and user preferences.
"""

import os
import json
from typing import Dict, Optional, Any
from PySide2 import QtWidgets


# Default configuration
DEFAULT_CONFIG = {
    "provider": "openai",
    "model": None,  # None means use provider's default
    "temperature": 0.3,
    "max_tokens": 4000,
}

# Configuration file location
CONFIG_DIR = os.path.expanduser("~")
CONFIG_FILE = os.path.join(CONFIG_DIR, ".gptcad_config.json")
API_KEYS_FILE = os.path.join(CONFIG_DIR, ".gptcad_keys.json")


class Config:
    """Configuration manager for GPT-CAD."""

    _instance = None

    def __new__(cls):
        """Singleton pattern to ensure single config instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._config: Dict[str, Any] = DEFAULT_CONFIG.copy()
        self._api_keys: Dict[str, str] = {}
        self.load()

    def load(self):
        """Load configuration from files."""
        # Load main config
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    saved_config = json.load(f)
                    self._config.update(saved_config)
            except (json.JSONDecodeError, IOError):
                pass  # Use defaults if file is corrupted

        # Load API keys
        if os.path.exists(API_KEYS_FILE):
            try:
                with open(API_KEYS_FILE, "r") as f:
                    self._api_keys = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        # Migrate old API key format (single file)
        self._migrate_old_api_key()

    def _migrate_old_api_key(self):
        """Migrate from old single api_key.txt to new format."""
        old_key_file = os.path.join(CONFIG_DIR, "api_key.txt")
        if os.path.exists(old_key_file) and "openai" not in self._api_keys:
            try:
                with open(old_key_file, "r") as f:
                    old_key = f.read().strip()
                    if old_key:
                        self._api_keys["openai"] = old_key
                        self.save()
            except IOError:
                pass

    def save(self):
        """Save configuration to files."""
        # Save main config
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self._config, f, indent=2)
        except IOError as e:
            print(f"Warning: Could not save config: {e}")

        # Save API keys
        try:
            with open(API_KEYS_FILE, "w") as f:
                json.dump(self._api_keys, f, indent=2)
            # Set restrictive permissions on keys file
            os.chmod(API_KEYS_FILE, 0o600)
        except IOError as e:
            print(f"Warning: Could not save API keys: {e}")

    @property
    def provider(self) -> str:
        """Get the current provider name."""
        return self._config.get("provider", "openai")

    @provider.setter
    def provider(self, value: str):
        """Set the current provider."""
        self._config["provider"] = value
        self.save()

    @property
    def model(self) -> Optional[str]:
        """Get the current model (None for provider default)."""
        return self._config.get("model")

    @model.setter
    def model(self, value: Optional[str]):
        """Set the current model."""
        self._config["model"] = value
        self.save()

    @property
    def temperature(self) -> float:
        """Get the temperature setting."""
        return self._config.get("temperature", 0.3)

    @temperature.setter
    def temperature(self, value: float):
        """Set the temperature."""
        self._config["temperature"] = max(0.0, min(1.0, value))
        self.save()

    @property
    def max_tokens(self) -> int:
        """Get the max tokens setting."""
        return self._config.get("max_tokens", 4000)

    @max_tokens.setter
    def max_tokens(self, value: int):
        """Set max tokens."""
        self._config["max_tokens"] = value
        self.save()

    def get_api_key(self, provider: str) -> Optional[str]:
        """Get API key for a specific provider."""
        return self._api_keys.get(provider)

    def set_api_key(self, provider: str, api_key: str):
        """Set API key for a specific provider."""
        self._api_keys[provider] = api_key
        self.save()

    def has_api_key(self, provider: str) -> bool:
        """Check if an API key exists for a provider."""
        return provider in self._api_keys and bool(self._api_keys[provider])


def ask_for_api_key(provider_name: str, display_name: str) -> Optional[str]:
    """
    Prompt user for an API key via GUI dialog.

    Args:
        provider_name: Internal provider name (e.g., 'openai', 'claude')
        display_name: Display name for the dialog (e.g., 'OpenAI', 'Anthropic Claude')

    Returns:
        The API key if provided, None otherwise
    """
    api_key, ok = QtWidgets.QInputDialog.getText(
        None,
        f"{display_name} API Key",
        f"Enter your {display_name} API key:",
        QtWidgets.QLineEdit.Password  # Hide the key as it's typed
    )

    if ok and api_key:
        config = Config()
        config.set_api_key(provider_name, api_key)
        return api_key

    return None


# Global config instance
def get_config() -> Config:
    """Get the global configuration instance."""
    return Config()
