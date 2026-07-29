"""Template library of ready-made parametric starter programs.

A template is a plain IR program (see :mod:`.schema`) with clean named
features and engineering-sane default dimensions (mm). Selecting one pre-fills
the editable plan preview or seeds the engineering timeline with no API call;
the user then tweaks parameters or asks the AI to modify it.

User templates are JSON files ``{"name": ..., "description": ...,
"operations": [...]}`` stored in :func:`user_templates_dir` (override with the
``GPT4FREECAD_TEMPLATES`` environment variable). Like the rest of the core,
this module is FreeCAD-free and stdlib-only so it can be unit-tested directly.
"""

from __future__ import annotations

import copy
import json
import os
import re
from typing import Any, Dict, List, Tuple

from . import schema

BUILTIN_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "flange",
        "name": "Flange",
        "description": "Circular pipe flange: disc + hub, 20 mm centre bore, "
                       "6x M6 clearance holes on a 60 mm bolt circle.",
        "operations": [
            {"op": "cylinder", "name": "disc", "radius": 40, "height": 8},
            {"op": "cylinder", "name": "hub", "radius": 16, "height": 26},
            {"op": "fuse", "name": "flange_body", "parts": ["disc", "hub"]},
            {"op": "hole", "name": "bored_body", "target": "flange_body",
             "diameter": 20, "depth": 30, "position": [0, 0, 26], "through": True},
            {"op": "cylinder", "name": "bolt_hole", "radius": 3.3, "height": 10,
             "placement": {"pos": [30, 0, -1]}},
            {"op": "polar_pattern", "name": "bolt_holes", "source": "bolt_hole",
             "count": 6},
            {"op": "cut", "name": "flange", "base": "bored_body", "tool": "bolt_holes"},
        ],
    },
    {
        "id": "l-bracket",
        "name": "L-bracket",
        "description": "90-degree bracket, 60x40 base and 46 mm upright, 6 mm "
                       "thick, two M5 clearance holes per leg.",
        "operations": [
            {"op": "box", "name": "base_plate", "length": 60, "width": 40, "height": 6},
            {"op": "box", "name": "upright", "length": 6, "width": 40, "height": 46},
            {"op": "fuse", "name": "bracket_body", "parts": ["base_plate", "upright"]},
            {"op": "hole", "name": "base_holes_1", "target": "bracket_body",
             "diameter": 5.5, "depth": 10, "position": [40, 10, 6], "through": True},
            {"op": "hole", "name": "base_holes_2", "target": "base_holes_1",
             "diameter": 5.5, "depth": 10, "position": [40, 30, 6], "through": True},
            {"op": "hole", "name": "wall_holes_1", "target": "base_holes_2",
             "diameter": 5.5, "depth": 10, "position": [6, 10, 36],
             "axis": [-1, 0, 0], "through": True},
            {"op": "hole", "name": "bracket", "target": "wall_holes_1",
             "diameter": 5.5, "depth": 10, "position": [6, 30, 36],
             "axis": [-1, 0, 0], "through": True},
        ],
    },
    {
        "id": "enclosure",
        "name": "Open-top enclosure",
        "description": "80x60x40 electronics enclosure, 2.5 mm walls, open top, "
                       "four corner screw bosses on the floor.",
        "operations": [
            {"op": "box", "name": "body", "length": 80, "width": 60, "height": 40},
            {"op": "shell", "name": "shelled", "source": "body", "thickness": 2.5},
            {"op": "cylinder", "name": "boss", "radius": 3.5, "height": 8,
             "placement": {"pos": [8, 8, 2.5]}},
            {"op": "linear_pattern", "name": "bosses", "source": "boss",
             "direction": [1, 0, 0], "count": 2, "spacing": 64,
             "direction2": [0, 1, 0], "count2": 2, "spacing2": 44},
            {"op": "fuse", "name": "enclosure", "parts": ["shelled", "bosses"]},
        ],
    },
    {
        "id": "gear-blank",
        "name": "Gear blank",
        "description": "60 mm gear blank: rim + 24 mm hub, 8 mm centre bore, "
                       "ready for teeth or a keyway.",
        "operations": [
            {"op": "cylinder", "name": "rim", "radius": 30, "height": 10},
            {"op": "cylinder", "name": "hub", "radius": 12, "height": 18},
            {"op": "fuse", "name": "blank_body", "parts": ["rim", "hub"]},
            {"op": "hole", "name": "gear_blank", "target": "blank_body",
             "diameter": 8, "depth": 20, "position": [0, 0, 18], "through": True},
        ],
    },
    {
        "id": "mounting-plate",
        "name": "Mounting plate",
        "description": "100x80x6 plate with four M4 clearance holes on a "
                       "80x60 pattern, 10 mm from each edge.",
        "operations": [
            {"op": "box", "name": "plate", "length": 100, "width": 80, "height": 6},
            {"op": "cylinder", "name": "corner_hole", "radius": 2.25, "height": 8,
             "placement": {"pos": [10, 10, -1]}},
            {"op": "linear_pattern", "name": "corner_holes", "source": "corner_hole",
             "direction": [1, 0, 0], "count": 2, "spacing": 80,
             "direction2": [0, 1, 0], "count2": 2, "spacing2": 60},
            {"op": "cut", "name": "mounting_plate", "base": "plate",
             "tool": "corner_holes"},
        ],
    },
    {
        "id": "spacer",
        "name": "Spacer / standoff",
        "description": "10 mm diameter x 12 mm cylindrical spacer with an "
                       "M3 clearance bore.",
        "operations": [
            {"op": "cylinder", "name": "body", "radius": 5, "height": 12},
            {"op": "hole", "name": "spacer", "target": "body",
             "diameter": 3.4, "depth": 14, "position": [0, 0, 12], "through": True},
        ],
    },
]


def builtin_templates() -> List[Dict[str, Any]]:
    """The built-in template list (do not mutate; use :func:`template_program`)."""
    return list(BUILTIN_TEMPLATES)


def template_program(template: Dict[str, Any]) -> Dict[str, Any]:
    """A deep-copied ``{"operations": [...]}`` program, safe to edit."""
    return {"operations": copy.deepcopy(template["operations"])}


# --------------------------------------------------------------------------- #
# User templates (JSON files on disk)
# --------------------------------------------------------------------------- #
def user_templates_dir() -> str:
    """Folder holding user-saved templates.

    ``GPT4FREECAD_TEMPLATES`` overrides; inside FreeCAD the folder lives in the
    user application data directory, elsewhere under the home directory.
    """
    override = os.environ.get("GPT4FREECAD_TEMPLATES")
    if override:
        return override
    try:
        import FreeCAD as App

        return os.path.join(App.getUserAppDataDir(), "GPT4FreeCAD", "templates")
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".gpt4freecad_templates")


def user_templates() -> Tuple[List[Dict[str, Any]], List[str]]:
    """Load user templates from disk.

    Returns ``(templates, problems)`` where ``problems`` are one-line messages
    for files that could not be loaded (bad JSON, invalid program). A missing
    folder is not an error.
    """
    folder = user_templates_dir()
    templates: List[Dict[str, Any]] = []
    problems: List[str] = []
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return templates, problems
    for entry in entries:
        if not entry.lower().endswith(".json"):
            continue
        path = os.path.join(folder, entry)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            operations = schema.validate_program(
                data.get("operations", data) if isinstance(data, dict) else data)
        except Exception as exc:  # noqa: BLE001 - report and keep loading
            problems.append(f"{entry}: {exc}")
            continue
        stem = os.path.splitext(entry)[0]
        name = data.get("name") if isinstance(data, dict) else None
        templates.append({
            "id": stem,
            "name": str(name or stem),
            "description": (data.get("description", "") if isinstance(data, dict)
                            else "") or "User template",
            "operations": operations,
            "user": True,
            "path": path,
        })
    return templates, problems


def save_user_template(name: str, operations: List[Dict[str, Any]],
                       description: str = "") -> str:
    """Validate and save ``operations`` as a user template; return the path.

    The filename is a slug of ``name``; saving under an existing name replaces
    that template. Raises :class:`schema.SchemaError` on an invalid program and
    ``ValueError`` on an empty name.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Template name must not be empty.")
    operations = schema.validate_program({"operations": operations})
    folder = user_templates_dir()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, _slug(name) + ".json")
    payload = {"name": name, "description": description, "operations": operations}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return path


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "template"
