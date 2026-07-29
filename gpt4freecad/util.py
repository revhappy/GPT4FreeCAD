"""Small pure helpers shared across FreeCAD and non-FreeCAD modules."""

from __future__ import annotations

import re

_CODE_FENCE = re.compile(r"```(?:python|py)?\s*(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    """Return the Python from a model reply, preferring a fenced block."""
    match = _CODE_FENCE.search(text or "")
    if match:
        return match.group(1).strip()
    return (text or "").strip()
