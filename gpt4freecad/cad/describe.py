"""Plain-English descriptions of IR operations.

The plan the model returns is JSON, which is exact, diffable and editable -
and unreadable at a glance. ``{"op": "cylinder", "name": "bore", "radius": 6,
"height": 12}`` is four facts a person has to assemble in their head before
they know whether to press Build. ``Ø12 x 12 mm`` is the same four facts,
already assembled.

So this module turns a program into rows a person can scan, and the panel shows
them above the JSON rather than instead of it: the table to read, the JSON to
edit. Everything here is pure - no Qt, no FreeCAD - so the wording is
unit-testable and the descriptions cannot drift from :mod:`~gpt4freecad.cad.schema`
without a test noticing.

Tolerance is deliberate. These rows are rendered from whatever is in the plan
box, which during an edit is half-typed and after a bad reply may be nonsense.
Nothing here validates: an unknown op, a missing field or a string where a
number belongs all render as *something*, because a table that goes blank is
worse than a table that says the plan is odd.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, NamedTuple

from . import schema

# The vectors worth naming. Anything else is shown as its numbers, which is
# honest: "[1, 1, 0]" says more than a made-up name for a diagonal would.
_AXIS_NAMES = {
    (1, 0, 0): "+X", (-1, 0, 0): "-X",
    (0, 1, 0): "+Y", (0, -1, 0): "-Y",
    (0, 0, 1): "+Z", (0, 0, -1): "-Z",
}

_TIMES = "×"    # ×
_DIA = "Ø"      # Ø
_ARROW = "→"    # →
_DEG = "°"      # °


class Row(NamedTuple):
    """One line of the plan table."""

    index: int      # 1-based step number
    op: str         # operation name, as written in the plan
    name: str       # the object this step produces ("" for in-place ops)
    detail: str     # the human description
    result: bool    # True if nothing later consumes this object
    source: str     # this step's JSON, for the tooltip


# --------------------------------------------------------------------------- #
# Value formatting
# --------------------------------------------------------------------------- #
def _num(value: Any) -> str:
    """A number the way a person would write it: 10, 2.5, not 10.0000."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return f"{value:g}"


def _vec(value: Any) -> str:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return "(" + ", ".join(_num(v) for v in value) + ")"
    return str(value)


def _axis(value: Any) -> str:
    """'+Z' for the six cardinal directions, the raw numbers otherwise."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            key = tuple(1 if v > 0 else (-1 if v < 0 else 0) for v in value)
        except TypeError:
            return _vec(value)
        # Only name it when a single component carries the whole direction.
        if sum(abs(k) for k in key) == 1 and key in _AXIS_NAMES:
            return _AXIS_NAMES[key]
    return _vec(value)


def _angle(value: Any) -> str:
    return f"{_num(value)}{_DEG}"


def _dia(value: Any) -> str:
    """Radii are how the IR stores it; diameters are how parts are specified."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"{_DIA}{value}"
    return f"{_DIA}{_num(value * 2)}"


def _names(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return " + ".join(str(v) for v in value)
    return str(value)


def _placement(op: Dict[str, Any]) -> str:
    """The 'at (x, y, z)' / 'turned 45° about +Z' tail, or ''."""
    placement = op.get("placement")
    if not isinstance(placement, dict):
        return ""
    parts = []
    pos = placement.get("pos")
    if pos is not None:
        parts.append(f"at {_vec(pos)}")
    rotation = placement.get("rotation")
    if isinstance(rotation, dict):
        parts.append(f"turned {_angle(rotation.get('angle'))} "
                     f"about {_axis(rotation.get('axis'))}")
    return (", " + ", ".join(parts)) if parts else ""


def _slice(op: Dict[str, Any]) -> str:
    """Cylinders and revolves take a partial sweep; a full one says nothing."""
    angle = op.get("angle")
    if angle is None or angle == 360:
        return ""
    return f", {_angle(angle)} slice"


def _edges(op: Dict[str, Any]) -> str:
    edges = op.get("edges")
    if not isinstance(edges, list) or not edges:
        return "all edges"
    if len(edges) == 1:
        return f"edge {edges[0]}"
    return "edges " + ", ".join(str(e) for e in edges)


def _points(profile: Any) -> str:
    count = len(profile) if isinstance(profile, list) else 0
    return f"{count}-point outline"


# --------------------------------------------------------------------------- #
# Per-operation descriptions
# --------------------------------------------------------------------------- #
def _box(op, unit):
    return (f"{_num(op.get('length'))} {_TIMES} {_num(op.get('width'))} "
            f"{_TIMES} {_num(op.get('height'))} {unit}{_placement(op)}")


def _cylinder(op, unit):
    return (f"{_dia(op.get('radius'))} {_TIMES} {_num(op.get('height'))} {unit}"
            f"{_slice(op)}{_placement(op)}")


def _sphere(op, unit):
    return f"{_dia(op.get('radius'))} {unit}{_placement(op)}"


def _cone(op, unit):
    return (f"{_dia(op.get('radius1'))} {_ARROW} {_dia(op.get('radius2'))}, "
            f"{_num(op.get('height'))} {unit} tall{_placement(op)}")


def _torus(op, unit):
    return (f"ring {_dia(op.get('radius1'))}, tube {_dia(op.get('radius2'))} "
            f"{unit}{_placement(op)}")


def _extrude(op, unit):
    return (f"{_points(op.get('profile'))}, {_num(op.get('height'))} {unit} "
            f"tall{_placement(op)}")


def _revolve(op, unit):
    angle = op.get("angle")
    swept = _angle(angle) if angle is not None else f"360{_DEG}"
    return (f"{_points(op.get('profile'))} revolved {swept}{_placement(op)}")


def _cut(op, unit):
    return f"{op.get('base')} minus {op.get('tool')}"


def _fuse(op, unit):
    return _names(op.get("parts"))


def _common(op, unit):
    parts = op.get("parts")
    joined = (" and ".join(str(p) for p in parts)
              if isinstance(parts, (list, tuple)) else str(parts))
    return f"overlap of {joined}"


def _linear_pattern(op, unit):
    text = (f"{_num(op.get('count'))} {_TIMES} {op.get('source')}, "
            f"{_num(op.get('spacing'))} {unit} apart along "
            f"{_axis(op.get('direction'))}")
    if op.get("count2"):
        text += (f"; {_num(op.get('count2'))} {_TIMES} "
                 f"{_num(op.get('spacing2'))} {unit} along "
                 f"{_axis(op.get('direction2'))}")
    if op.get("fuse") is False:
        text += ", left separate"
    if op.get("keep_source"):
        text += ", original kept"
    return text


def _polar_pattern(op, unit):
    angle = op.get("angle")
    text = (f"{_num(op.get('count'))} {_TIMES} {op.get('source')} around "
            f"{_axis(op.get('axis', [0, 0, 1]))} over "
            f"{_angle(angle) if angle is not None else f'360{_DEG}'}")
    if op.get("center"):
        text += f" through {_vec(op['center'])}"
    if op.get("fuse") is False:
        text += ", left separate"
    if op.get("keep_source"):
        text += ", original kept"
    return text


def _mirror(op, unit):
    text = f"{op.get('source')} across {op.get('plane')}"
    if op.get("base"):
        text += f" through {_vec(op['base'])}"
    text += ", kept separate" if op.get("combine") is False else ", combined"
    return text


def _shell(op, unit):
    text = (f"{op.get('source')} hollowed to {_num(op.get('thickness'))} "
            f"{unit} walls")
    faces = op.get("open_faces")
    if isinstance(faces, list) and faces:
        text += f", faces {', '.join(str(f) for f in faces)} open"
    return text


def _hole(op, unit):
    # 'hole' is the one op specified as a diameter already - everything round
    # elsewhere in the IR is a radius.
    text = (f"{_DIA}{_num(op.get('diameter'))} {_TIMES} {_num(op.get('depth'))} "
            f"{unit} deep at {_vec(op.get('position'))} in {op.get('target')}")
    if op.get("through"):
        text += ", through"
    if op.get("axis"):
        text += f", along {_axis(op['axis'])}"
    if op.get("cbore_diameter"):
        text += (f", {_DIA}{_num(op['cbore_diameter'])} counterbore "
                 f"{_num(op.get('cbore_depth'))} {unit} deep")
    if op.get("csink_diameter"):
        text += (f", {_DIA}{_num(op['csink_diameter'])} countersink at "
                 f"{_angle(op.get('csink_angle'))}")
    return text


def _fillet(op, unit):
    return (f"{_num(op.get('radius'))} {unit} radius on {_edges(op)} of "
            f"{op.get('target')}")


def _chamfer(op, unit):
    return (f"{_num(op.get('size'))} {unit} bevel on {_edges(op)} of "
            f"{op.get('target')}")


def _translate(op, unit):
    return f"{op.get('target')} moved by {_vec(op.get('vector'))} {unit}"


def _rotate(op, unit):
    text = (f"{op.get('target')} turned {_angle(op.get('angle'))} about "
            f"{_axis(op.get('axis'))}")
    if op.get("center"):
        text += f" through {_vec(op['center'])}"
    return text


_DESCRIBERS = {
    "box": _box, "cylinder": _cylinder, "sphere": _sphere, "cone": _cone,
    "torus": _torus, "extrude": _extrude, "revolve": _revolve,
    "cut": _cut, "fuse": _fuse, "common": _common,
    "linear_pattern": _linear_pattern, "polar_pattern": _polar_pattern,
    "mirror": _mirror, "shell": _shell, "hole": _hole,
    "fillet": _fillet, "chamfer": _chamfer,
    "translate": _translate, "rotate": _rotate,
}


def _generic(op: Dict[str, Any], unit: str) -> str:
    """Last resort: every field but 'op' and 'name', as they were written.

    Reached by an operation this module has no wording for yet - a new op added
    to the schema, or one the model invented. Listing the fields keeps the row
    informative instead of empty.
    """
    parts = []
    for key, value in op.items():
        if key in ("op", "name"):
            continue
        if isinstance(value, (dict, list)):
            parts.append(f"{key} {json.dumps(value, separators=(',', ':'))}")
        else:
            parts.append(f"{key} {_num(value)}")
    return ", ".join(parts)


def describe(op: Any, unit: str = "mm") -> str:
    """One line saying what this operation makes, in the plan's own units."""
    if not isinstance(op, dict):
        return str(op)
    describer = _DESCRIBERS.get(op.get("op"))
    if describer is None:
        return _generic(op, unit)
    try:
        return describer(op, unit)
    except Exception:  # noqa: BLE001 - a half-typed op must still render
        return _generic(op, unit)


# --------------------------------------------------------------------------- #
# Whole programs
# --------------------------------------------------------------------------- #
def operations_of(data: Any) -> List[Any]:
    """The operation list inside a parsed plan, however it is wrapped.

    Accepts ``{"operations": [...]}`` and a bare list, the same two shapes
    :func:`schema.validate_program` accepts, and returns ``[]`` for anything
    else rather than raising - the caller is rendering, not validating.
    """
    if isinstance(data, dict):
        operations = data.get("operations")
    else:
        operations = data
    return list(operations) if isinstance(operations, list) else []


def _end_products(operations: List[Any]) -> set:
    """Names nothing later consumes. Empty if the program is too broken to tell."""
    try:
        return set(schema.leaf_names(operations))
    except Exception:  # noqa: BLE001
        return set()


def plan_rows(operations: Any, unit: str = "mm") -> List[Row]:
    """Describe a whole program, one :class:`Row` per operation."""
    operations = operations if isinstance(operations, list) else []
    leaves = _end_products(operations)
    rows = []
    for index, op in enumerate(operations, start=1):
        name = op.get("name") if isinstance(op, dict) else None
        name = name if isinstance(name, str) else ""
        op_name = op.get("op") if isinstance(op, dict) else None
        try:
            source = json.dumps(op, indent=2)
        except (TypeError, ValueError):
            source = str(op)
        rows.append(Row(
            index=index,
            op=str(op_name) if op_name is not None else "?",
            name=name,
            detail=describe(op, unit),
            result=bool(name) and name in leaves,
            source=source,
        ))
    return rows
