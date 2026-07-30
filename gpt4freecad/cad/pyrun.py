"""Advanced 'Python mode': run model-generated FreeCAD scripts.

This is the opt-in power-user path. It still ``exec``s model output, so it is
inherently less safe than the IR interpreter - we mitigate (not eliminate) the
risk with a static denylist for obviously dangerous calls and by running inside
an undo transaction. Imports FreeCAD; not used by the unit tests.
"""

from __future__ import annotations

import math
import traceback
from typing import List, Tuple

import FreeCAD as App
import Part
from FreeCAD import Base

from ..util import extract_code

try:
    import FreeCADGui as Gui
except Exception:  # pragma: no cover - headless
    Gui = None


class PythonRunError(Exception):
    pass


# Substrings that almost never belong in CAD scripts and that we refuse to run.
_DENY = [
    "import os", "import sys", "import subprocess", "import socket",
    "import shutil", "import requests", "import urllib", "import ctypes",
    "subprocess", "__import__", "eval(", "exec(", "compile(",
    "open(", "os.system", "os.popen", "Popen", "globals(", "getattr(__",
]


def _check_safety(code: str) -> None:
    lowered = code.lower()
    hits = [tok for tok in _DENY if tok.lower() in lowered]
    if hits:
        raise PythonRunError(
            "Refusing to run generated code: it contains potentially unsafe "
            f"calls ({', '.join(sorted(set(hits)))}). "
            "Use Structured mode, or edit the code manually if you trust it."
        )


def run_python_code(text_or_code: str, doc=None, prechecked: bool = False) -> Tuple[List[str], str]:
    """Execute model code. Returns ``(log_lines, code_that_ran)``.

    ``prechecked`` skips the denylist (used when the user has reviewed/edited the
    code themselves in the panel).
    """
    code = extract_code(text_or_code)
    if not code:
        raise PythonRunError("No Python code found in the model reply.")
    if not prechecked:
        _check_safety(code)

    if doc is None:
        doc = App.ActiveDocument or App.newDocument("Unnamed")

    namespace = {
        "App": App, "FreeCAD": App, "Part": Part, "Base": Base,
        "Gui": Gui, "FreeCADGui": Gui, "math": math, "doc": doc,
        "Vector": App.Vector, "Placement": App.Placement, "Rotation": App.Rotation,
    }

    doc.openTransaction("GPT4FreeCAD (python)")
    try:
        exec(compile(code, "<gpt4freecad>", "exec"), namespace)  # noqa: S102
        doc.recompute()
        doc.commitTransaction()
    except Exception as exc:  # noqa: BLE001
        doc.abortTransaction()
        raise PythonRunError(_describe_error(exc, code)) from exc

    return [f"Executed {len(code.splitlines())} lines of Python."], code


def _describe_error(exc: BaseException, code: str) -> str:
    """Error message pinned to the failing line of the generated script.

    'NameError: x is not defined' alone is useless for auto-repair; with the
    line number and source line the model can fix the exact spot.
    """
    lineno = None
    if isinstance(exc, SyntaxError) and exc.filename == "<gpt4freecad>":
        lineno = exc.lineno
    else:
        for frame in traceback.extract_tb(exc.__traceback__):
            if frame.filename == "<gpt4freecad>":
                lineno = frame.lineno  # innermost generated-code frame wins
    message = f"{type(exc).__name__}: {exc}"
    lines = code.splitlines()
    if lineno and 1 <= lineno <= len(lines):
        message += f"\n  at line {lineno}: {lines[lineno - 1].strip()}"
    return message
