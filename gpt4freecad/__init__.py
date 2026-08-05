"""GPT4FreeCAD - turn natural language into parametric FreeCAD geometry.

This package is intentionally split so that the *core* logic (LLM providers and
the CAD intermediate-representation schema) has **no dependency on FreeCAD or
PySide2**. That makes it unit-testable with a plain CPython interpreter, while
the ``ui`` / ``interpreter`` / ``workbench`` modules do the FreeCAD-specific work.

Layout::

    gpt4freecad/
        config.py          preferences + API-key storage (FreeCAD ParamGet)
        llm/               provider abstraction (OpenAI, Anthropic, Gemini, local)
        harness.py         auto-repair budget + loop guard               (pure)
        cad/schema.py      CAD operation IR + JSON schema + validation  (pure)
        cad/prompts.py     system prompts                               (pure)
        cad/interpreter.py IR -> parametric FreeCAD objects             (FreeCAD)
        ui/                dockable panel + settings dialog             (PySide)
        workbench.py       Gui.Workbench registration                  (FreeCAD)
"""

__version__ = "2.5.0"
__author__ = "Robb Sharma"
__all__ = ["__version__"]
