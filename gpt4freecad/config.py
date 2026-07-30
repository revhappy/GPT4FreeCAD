"""Preferences + API-key storage.

Uses FreeCAD's parameter system (``App.ParamGet``) when running inside FreeCAD,
and a JSON file fallback otherwise so the module imports and works under a plain
interpreter (and in tests). Keys live under the standard
``Preferences/Mod/GPT4FreeCAD`` group rather than a world-readable text file in
the home directory.
"""

from __future__ import annotations

import json
import os
from typing import Optional

_PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/GPT4FreeCAD"


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
class _ParamBackend:
    """Thin wrapper over a FreeCAD ParameterGrp handle."""

    def __init__(self, handle):
        self._h = handle

    def get_str(self, name, default=""):
        return self._h.GetString(name, default)

    def set_str(self, name, value):
        self._h.SetString(name, value)

    def get_float(self, name, default=0.0):
        return self._h.GetFloat(name, default)

    def set_float(self, name, value):
        self._h.SetFloat(name, float(value))

    def get_int(self, name, default=0):
        return self._h.GetInt(name, default)

    def set_int(self, name, value):
        self._h.SetInt(name, int(value))

    def get_bool(self, name, default=False):
        return self._h.GetBool(name, default)

    def set_bool(self, name, value):
        self._h.SetBool(name, bool(value))


class _JsonBackend:
    """Fallback used outside FreeCAD; persists to a JSON file."""

    def __init__(self, path):
        self._path = path
        self._data = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    self._data = json.load(fh)
            except Exception:
                self._data = {}

    def _save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
        except Exception:
            pass

    def get_str(self, name, default=""):
        return str(self._data.get(name, default))

    def set_str(self, name, value):
        self._data[name] = value
        self._save()

    def get_float(self, name, default=0.0):
        try:
            return float(self._data.get(name, default))
        except (TypeError, ValueError):
            return default

    def set_float(self, name, value):
        self._data[name] = float(value)
        self._save()

    def get_int(self, name, default=0):
        try:
            return int(self._data.get(name, default))
        except (TypeError, ValueError):
            return default

    def set_int(self, name, value):
        self._data[name] = int(value)
        self._save()

    def get_bool(self, name, default=False):
        return bool(self._data.get(name, default))

    def set_bool(self, name, value):
        self._data[name] = bool(value)
        self._save()


def _make_backend():
    try:
        import FreeCAD as App  # noqa: F401

        return _ParamBackend(App.ParamGet(_PARAM_PATH))
    except Exception:
        path = os.environ.get(
            "GPT4FREECAD_CONFIG",
            os.path.join(os.path.expanduser("~"), ".gpt4freecad.json"),
        )
        return _JsonBackend(path)


# --------------------------------------------------------------------------- #
# Config facade
# --------------------------------------------------------------------------- #
class Config:
    """Typed, named access to all GPT4FreeCAD settings."""

    def __init__(self, backend=None):
        self._b = backend if backend is not None else _make_backend()
        self._migrate_legacy_key()

    # --- providers / keys ------------------------------------------------- #
    def api_key(self, provider_id: str) -> str:
        key = self._b.get_str(f"key_{provider_id}", "").strip()
        if not key:
            # Standard env-var names: ANTHROPIC_API_KEY, GEMINI_API_KEY, ...
            key = os.environ.get(f"{provider_id.upper()}_API_KEY", "").strip()
        return key

    def set_api_key(self, provider_id: str, key: str) -> None:
        self._b.set_str(f"key_{provider_id}", (key or "").strip())

    def provider(self) -> str:
        return self._b.get_str("provider", "gemini")

    def set_provider(self, provider_id: str) -> None:
        self._b.set_str("provider", provider_id)

    def model(self, provider_id: str, default: str = "") -> str:
        return self._b.get_str(f"model_{provider_id}", default)

    def set_model(self, provider_id: str, model: str) -> None:
        self._b.set_str(f"model_{provider_id}", model)

    def openai_endpoint(self) -> str:
        return self._b.get_str(
            "openai_endpoint", "https://api.openai.com/v1/chat/completions"
        )

    def set_openai_endpoint(self, url: str) -> None:
        self._b.set_str("openai_endpoint", url)

    def machine_base_url(self) -> str:
        """Base URL of the local `machine serve` inference server."""
        return self._b.get_str("machine_base_url", "http://127.0.0.1:8177").strip() \
            or "http://127.0.0.1:8177"

    def set_machine_base_url(self, url: str) -> None:
        self._b.set_str("machine_base_url", (url or "").strip())

    def machine_model_path(self) -> str:
        """Local .gguf to activate on demand. Empty = attach to a server only."""
        return self._b.get_str("machine_model_path", "").strip()

    def set_machine_model_path(self, path: str) -> None:
        self._b.set_str("machine_model_path", (path or "").strip())

    # --- generation params ------------------------------------------------ #
    def temperature(self) -> float:
        return self._b.get_float("temperature", 0.2)

    def set_temperature(self, value: float) -> None:
        self._b.set_float("temperature", value)

    def max_tokens(self) -> int:
        return self._b.get_int("max_tokens", 4096)

    def set_max_tokens(self, value: int) -> None:
        self._b.set_int("max_tokens", value)

    def thinking_level(self) -> str:
        """Gemini 3 reasoning depth: default|minimal|low|medium|high."""
        return self._b.get_str("thinking_level", "low")

    def set_thinking_level(self, value: str) -> None:
        self._b.set_str("thinking_level", value)

    # --- behaviour -------------------------------------------------------- #
    def mode(self) -> str:
        # "structured" | "engineering" | "python"
        return self._b.get_str("mode", "structured")

    def set_mode(self, value: str) -> None:
        self._b.set_str("mode", value)

    def part_layout(self) -> str:
        """How generated components relate: fused | separate."""
        value = self._b.get_str("part_layout", "fused")
        return value if value in ("fused", "separate") else "fused"

    def set_part_layout(self, value: str) -> None:
        self._b.set_str("part_layout", value if value in ("fused", "separate") else "fused")

    # --- 3D-print profile ------------------------------------------------- #
    def print_mode(self) -> bool:
        return self._b.get_bool("print_mode", False)

    def set_print_mode(self, value: bool) -> None:
        self._b.set_bool("print_mode", value)

    def bed_x(self) -> float:
        return self._b.get_float("bed_x", 254.0)  # 10 inches

    def bed_y(self) -> float:
        return self._b.get_float("bed_y", 254.0)

    def bed_z(self) -> float:
        return self._b.get_float("bed_z", 254.0)

    def set_bed(self, x: float, y: float, z: float) -> None:
        self._b.set_float("bed_x", x)
        self._b.set_float("bed_y", y)
        self._b.set_float("bed_z", z)

    def stl_deflection(self) -> float:
        """Linear mesh deviation (mm) used when exporting STL. Smaller = finer."""
        return self._b.get_float("stl_deflection", 0.1)

    def set_stl_deflection(self, value: float) -> None:
        self._b.set_float("stl_deflection", value)

    def print_profile(self):
        """Return {'bed': [x, y, z]} when print mode is on, else None."""
        if not self.print_mode():
            return None
        return {"bed": [self.bed_x(), self.bed_y(), self.bed_z()]}

    def units(self) -> str:
        return self._b.get_str("units", "mm")

    def set_units(self, value: str) -> None:
        self._b.set_str("units", value)

    def auto_run(self) -> bool:
        """If True, build immediately; if False, show the plan and wait for Build."""
        return self._b.get_bool("auto_run", True)

    def set_auto_run(self, value: bool) -> None:
        self._b.set_bool("auto_run", value)

    def repair_rounds(self) -> int:
        """Max automatic fix-it round-trips per user action (0 disables)."""
        return max(0, min(self._b.get_int("repair_rounds", 3), 10))

    def set_repair_rounds(self, value: int) -> None:
        self._b.set_int("repair_rounds", max(0, min(int(value), 10)))

    # --- legacy migration ------------------------------------------------- #
    def _migrate_legacy_key(self) -> None:
        if self._b.get_bool("migrated_legacy", False):
            return
        self._b.set_bool("migrated_legacy", True)
        legacy = os.path.join(os.path.expanduser("~"), "api_key.txt")
        if os.path.exists(legacy) and not self.api_key("openai"):
            try:
                with open(legacy, "r", encoding="utf-8") as fh:
                    key = fh.read().strip()
                if key:
                    self.set_api_key("openai", key)
            except Exception:
                pass


_INSTANCE: Optional[Config] = None


def get_config() -> Config:
    """Process-wide singleton config."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = Config()
    return _INSTANCE
