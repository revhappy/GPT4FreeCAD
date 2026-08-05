"""The CAD intermediate representation (IR).

A *program* is a JSON object ``{"operations": [ ... ]}`` where each operation is
a dict with an ``op`` key. The model produces this; :func:`validate_program`
checks it (structure, types, dimensions, and name references) before the
interpreter ever touches FreeCAD. Keeping validation here - free of any FreeCAD
import - means it can be unit-tested and gives the user precise error messages
that can be fed back to the model for a retry.

This module is intentionally declarative: :data:`OPERATIONS` drives validation,
the prompt reference text, and the JSON schema, so the three never drift apart.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# Field type tokens used by the spec table below.
NUMBER = "number"        # int or float
INT = "int"              # whole number (counts)
BOOL = "bool"            # true / false
ENUM = "enum"            # one of a fixed set (allowed values in the op's "enums" map)
STRING = "string"        # non-empty str
VEC3 = "vec3"            # [x, y, z] numbers
INTLIST = "intlist"      # [int, ...]
STRLIST = "strlist"      # [str, ...] (>= 2 for booleans)
PROFILE = "profile"      # [[x, y], ...] (>= 3 points)
PLACEMENT = "placement"  # {pos?: vec3, rotation?: {axis: vec3, angle: number}}


class SchemaError(Exception):
    """Raised when an IR program is structurally invalid."""


# Each op: required fields, optional fields, whether it defines a new object,
# and which fields hold references to previously-defined object names.
# ``positive`` lists numeric fields that must be > 0.
OPERATIONS: Dict[str, Dict[str, Any]] = {
    "box": {
        "doc": "Axis-aligned box. length=X, width=Y, height=Z.",
        "required": {"name": STRING, "length": NUMBER, "width": NUMBER, "height": NUMBER},
        "optional": {"placement": PLACEMENT},
        "defines": True,
        "refs": [],
        "positive": ["length", "width", "height"],
    },
    "cylinder": {
        "doc": "Cylinder along +Z. Optional 'angle' (deg, <360) makes a pie slice.",
        "required": {"name": STRING, "radius": NUMBER, "height": NUMBER},
        "optional": {"angle": NUMBER, "placement": PLACEMENT},
        "defines": True,
        "refs": [],
        "positive": ["radius", "height"],
    },
    "sphere": {
        "doc": "Sphere centred at the placement origin.",
        "required": {"name": STRING, "radius": NUMBER},
        "optional": {"placement": PLACEMENT},
        "defines": True,
        "refs": [],
        "positive": ["radius"],
    },
    "cone": {
        "doc": "Truncated cone along +Z. radius1=bottom, radius2=top (0 = point).",
        "required": {"name": STRING, "radius1": NUMBER, "radius2": NUMBER, "height": NUMBER},
        "optional": {"placement": PLACEMENT},
        "defines": True,
        "refs": [],
        "positive": ["height"],
    },
    "torus": {
        "doc": "Torus. radius1=ring radius (centre to tube centre), radius2=tube radius.",
        "required": {"name": STRING, "radius1": NUMBER, "radius2": NUMBER},
        "optional": {"placement": PLACEMENT},
        "defines": True,
        "refs": [],
        "positive": ["radius1", "radius2"],
    },
    "extrude": {
        "doc": "Extrude a closed 2D polygon (XY plane) by 'height' along +Z. "
               "profile = list of [x, y] points, >= 3, do not repeat the first point.",
        "required": {"name": STRING, "profile": PROFILE, "height": NUMBER},
        "optional": {"placement": PLACEMENT},
        "defines": True,
        "refs": [],
        "positive": ["height"],
    },
    "revolve": {
        "doc": "Revolve a closed 2D profile around the Z axis - the lathe-style op for "
               "flanges, shafts, pulleys, vases. profile points are [r, z]: r = distance "
               "from the axis (>= 0), z = height. At least 3 points, do not repeat the "
               "first point. Optional 'angle' (deg, default 360) for a partial revolve.",
        "required": {"name": STRING, "profile": PROFILE},
        "optional": {"angle": NUMBER, "placement": PLACEMENT},
        "defines": True,
        "refs": [],
        "positive": ["angle"],
    },
    "cut": {
        "doc": "Boolean difference: base minus tool.",
        "required": {"name": STRING, "base": STRING, "tool": STRING},
        "optional": {},
        "defines": True,
        "refs": ["base", "tool"],
    },
    "fuse": {
        "doc": "Boolean union of two or more parts.",
        "required": {"name": STRING, "parts": STRLIST},
        "optional": {},
        "defines": True,
        "refs": ["parts"],
    },
    "common": {
        "doc": "Boolean intersection (common volume) of two or more parts.",
        "required": {"name": STRING, "parts": STRLIST},
        "optional": {},
        "defines": True,
        "refs": ["parts"],
    },
    "linear_pattern": {
        "doc": "Replicate 'source' along 'direction' (count copies, 'spacing' apart). Add "
               "direction2/count2/spacing2 for a 2D grid. Copies are fused into one solid "
               "unless fuse=false; the original is hidden unless keep_source=true.",
        "required": {"name": STRING, "source": STRING, "direction": VEC3,
                     "count": INT, "spacing": NUMBER},
        "optional": {"direction2": VEC3, "count2": INT, "spacing2": NUMBER,
                     "fuse": BOOL, "keep_source": BOOL},
        "defines": True,
        "refs": ["source"],
        "positive": ["count", "spacing"],
    },
    "polar_pattern": {
        "doc": "Replicate 'source' around 'axis' (default +Z) through 'center' (default "
               "origin): 'count' copies spread over 'angle' degrees (default 360 = full ring). "
               "Fused unless fuse=false.",
        "required": {"name": STRING, "source": STRING, "count": INT},
        "optional": {"angle": NUMBER, "axis": VEC3, "center": VEC3,
                     "fuse": BOOL, "keep_source": BOOL},
        "defines": True,
        "refs": ["source"],
        "positive": ["count"],
    },
    "mirror": {
        "doc": "Mirror 'source' across a plane (XY|XZ|YZ) through 'base' (default origin). "
               "combine=true (default) fuses the mirror with the original for symmetry.",
        "required": {"name": STRING, "source": STRING, "plane": ENUM},
        "optional": {"base": VEC3, "combine": BOOL},
        "defines": True,
        "refs": ["source"],
        "enums": {"plane": ["XY", "XZ", "YZ"]},
    },
    "shell": {
        "doc": "Hollow out 'source' to wall 'thickness'. open_faces = 1-based face indices to "
               "leave open (default: open the top +Z face).",
        "required": {"name": STRING, "source": STRING, "thickness": NUMBER},
        "optional": {"open_faces": INTLIST},
        "defines": True,
        "refs": ["source"],
        "positive": ["thickness"],
    },
    "hole": {
        "doc": "Drill a hole into 'target' at 'position' (centre of the hole's top), along "
               "'axis' (default -Z = downward). through=true cuts all the way through. Optional "
               "counterbore (cbore_diameter/cbore_depth) or countersink (csink_diameter/"
               "csink_angle).",
        "required": {"name": STRING, "target": STRING, "diameter": NUMBER,
                     "depth": NUMBER, "position": VEC3},
        "optional": {"through": BOOL, "axis": VEC3,
                     "cbore_diameter": NUMBER, "cbore_depth": NUMBER,
                     "csink_diameter": NUMBER, "csink_angle": NUMBER},
        "defines": True,
        "refs": ["target"],
        "positive": ["diameter", "depth"],
    },
    "fillet": {
        "doc": "Round edges of 'target'. Omit 'edges' to fillet all edges, or give "
               "1-based edge indices.",
        "required": {"name": STRING, "target": STRING, "radius": NUMBER},
        "optional": {"edges": INTLIST},
        "defines": True,
        "refs": ["target"],
        "positive": ["radius"],
    },
    "chamfer": {
        "doc": "Bevel edges of 'target'. Omit 'edges' to chamfer all edges.",
        "required": {"name": STRING, "target": STRING, "size": NUMBER},
        "optional": {"edges": INTLIST},
        "defines": True,
        "refs": ["target"],
        "positive": ["size"],
    },
    "translate": {
        "doc": "Move an existing object by vector [dx, dy, dz] (mutates it in place).",
        "required": {"target": STRING, "vector": VEC3},
        "optional": {},
        "defines": False,
        "refs": ["target"],
    },
    "rotate": {
        "doc": "Rotate an existing object 'angle' degrees about 'axis' "
               "(optional 'center', default origin). Mutates it in place.",
        "required": {"target": STRING, "axis": VEC3, "angle": NUMBER},
        "optional": {"center": VEC3},
        "defines": False,
        "refs": ["target"],
    },
}


# --------------------------------------------------------------------------- #
# Type checks
# --------------------------------------------------------------------------- #
def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_field(op: str, field: str, kind: str, value: Any, allowed=None) -> None:
    where = f"operation '{op}', field '{field}'"
    if kind == NUMBER:
        if not _is_number(value):
            raise SchemaError(f"{where} must be a number, got {value!r}.")
    elif kind == INT:
        if not (_is_number(value) and float(value).is_integer()):
            raise SchemaError(f"{where} must be a whole number, got {value!r}.")
    elif kind == BOOL:
        if not isinstance(value, bool):
            raise SchemaError(f"{where} must be true or false, got {value!r}.")
    elif kind == ENUM:
        choices = allowed or []
        if not (isinstance(value, str) and value.upper() in {c.upper() for c in choices}):
            raise SchemaError(f"{where} must be one of {choices}, got {value!r}.")
    elif kind == STRING:
        if not isinstance(value, str) or not value.strip():
            raise SchemaError(f"{where} must be a non-empty string, got {value!r}.")
    elif kind == VEC3:
        if not (isinstance(value, (list, tuple)) and len(value) == 3 and all(_is_number(x) for x in value)):
            raise SchemaError(f"{where} must be [x, y, z] numbers, got {value!r}.")
    elif kind == INTLIST:
        if not (isinstance(value, list) and all(isinstance(x, int) and not isinstance(x, bool) for x in value)):
            raise SchemaError(f"{where} must be a list of integers, got {value!r}.")
    elif kind == STRLIST:
        if not (isinstance(value, list) and len(value) >= 2 and all(isinstance(x, str) and x.strip() for x in value)):
            raise SchemaError(f"{where} must be a list of >= 2 object names, got {value!r}.")
    elif kind == PROFILE:
        if not (isinstance(value, list) and len(value) >= 3):
            raise SchemaError(f"{where} must be a list of >= 3 [x, y] points, got {value!r}.")
        for pt in value:
            if not (isinstance(pt, (list, tuple)) and len(pt) == 2 and all(_is_number(x) for x in pt)):
                raise SchemaError(f"{where}: each point must be [x, y] numbers, got {pt!r}.")
    elif kind == PLACEMENT:
        _check_placement(op, value)
    else:  # pragma: no cover - guards against spec typos
        raise SchemaError(f"Internal: unknown field kind '{kind}' for {where}.")


def _check_placement(op: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise SchemaError(f"operation '{op}', field 'placement' must be an object.")
    if "pos" in value:
        _check_field(op, "placement.pos", VEC3, value["pos"])
    if "rotation" in value:
        rot = value["rotation"]
        if not isinstance(rot, dict):
            raise SchemaError(f"operation '{op}', 'placement.rotation' must be an object.")
        _check_field(op, "placement.rotation.axis", VEC3, rot.get("axis"))
        _check_field(op, "placement.rotation.angle", NUMBER, rot.get("angle"))


# --------------------------------------------------------------------------- #
# Semantic checks
#
# The type checks above reject a program the interpreter cannot execute. These
# reject one it executes into the *wrong solid*. OCC accepts several
# out-of-range values without complaint and quietly substitutes its own, so
# nothing downstream ever notices: measured against FreeCAD 1.1, a cylinder
# 'angle' of 0, -30 or 400 all build a full 360 cylinder; a revolve of 400
# degrees builds 40; a cone with radius1 = -5 builds one with radius1 = 0. A
# profile with a repeated point silently loses a corner (a four-point square
# became a triangle), and collinear or self-crossing profiles build a
# zero-volume solid. Catching these here is the only place they can be caught
# with a message that says what was wrong.
# --------------------------------------------------------------------------- #
_POINT_TOL = 1e-7   # OCC's coincident-point tolerance
_AREA_TOL = 1e-9


def _check_sweep_angle(op: Dict[str, Any]) -> None:
    """A cylinder/revolve 'angle' outside (0, 360] is silently rewritten by OCC."""
    angle = op.get("angle")
    if angle is None or not _is_number(angle):
        return
    if not 0 < angle <= 360:
        raise SchemaError(
            f"operation '{op['op']}', field 'angle' must be > 0 and <= 360 "
            f"degrees, got {angle}. FreeCAD does not reject an out-of-range "
            "angle - it silently builds a different shape (0 or a negative "
            "angle becomes a full revolution, 400 becomes 40). Omit 'angle' "
            "for a full 360."
        )


def _check_cylinder(op: Dict[str, Any]) -> None:
    _check_sweep_angle(op)


def _check_cone(op: Dict[str, Any]) -> None:
    for field in ("radius1", "radius2"):
        value = op.get(field)
        if _is_number(value) and value < 0:
            raise SchemaError(
                f"operation 'cone', field '{field}' must be >= 0, got {value}. "
                "A negative radius is silently clamped to 0, which builds a "
                "point-tipped cone instead of the one you described."
            )
    if not (op.get("radius1") or op.get("radius2")):
        raise SchemaError(
            "operation 'cone' needs at least one non-zero radius; "
            "radius1 and radius2 are both 0, which builds no geometry."
        )


def _check_torus(op: Dict[str, Any]) -> None:
    r1, r2 = op.get("radius1"), op.get("radius2")
    if _is_number(r1) and _is_number(r2) and r2 >= r1:
        raise SchemaError(
            f"operation 'torus' needs radius2 < radius1, got radius1={r1}, "
            f"radius2={r2}. radius1 is the ring radius (centre of the torus to "
            "centre of the tube) and radius2 is the tube radius, so a tube at "
            "least as thick as the ring leaves no hole and builds nothing."
        )


def _check_extrude(op: Dict[str, Any]) -> None:
    problem = _profile_problem(op.get("profile"))
    if problem:
        raise SchemaError(f"operation 'extrude', field 'profile': {problem}")


def _check_revolve(op: Dict[str, Any]) -> None:
    _check_sweep_angle(op)
    profile = op.get("profile")
    if isinstance(profile, list):
        for point in profile:
            if isinstance(point, (list, tuple)) and len(point) == 2 and _is_number(point[0]):
                if point[0] < 0:
                    raise SchemaError(
                        "operation 'revolve', field 'profile': point "
                        f"{list(point)} has r < 0. r is the distance from the Z "
                        "axis, so it cannot be negative - keep the whole "
                        "profile on one side of the axis."
                    )
    problem = _profile_problem(profile)
    if problem:
        raise SchemaError(f"operation 'revolve', field 'profile': {problem}")


_SEMANTIC = {
    "cylinder": _check_cylinder,
    "cone": _check_cone,
    "torus": _check_torus,
    "extrude": _check_extrude,
    "revolve": _check_revolve,
}


# --------------------------------------------------------------------------- #
# 2D profile geometry (pure - the interpreter closes the wire itself)
# --------------------------------------------------------------------------- #
def _same_point(a, b) -> bool:
    return abs(a[0] - b[0]) <= _POINT_TOL and abs(a[1] - b[1]) <= _POINT_TOL


def _signed_area(points) -> float:
    """Shoelace area; near zero means the points enclose nothing."""
    total = 0.0
    for i, (x1, y1) in enumerate(points):
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _sign(value: float) -> int:
    return 0 if abs(value) <= 1e-12 else (1 if value > 0 else -1)


def _turn(a, b, c) -> float:
    """Cross product of ab x ac - which side of the line ab the point c is on."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _crossing(points):
    """First pair of non-adjacent edges that properly cross, or None.

    Only *proper* crossings count (both pairs strictly straddle), so edges that
    merely touch at a shared vertex are not reported.
    """
    n = len(points)
    for i in range(n):
        a, b = points[i], points[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue  # adjacent edges always share a vertex
            c, d = points[j], points[(j + 1) % n]
            if (_sign(_turn(c, d, a)) * _sign(_turn(c, d, b)) < 0
                    and _sign(_turn(a, b, c)) * _sign(_turn(a, b, d)) < 0):
                return i + 1, j + 1
    return None


def _profile_problem(profile: Any) -> str:
    """Why this closed polygon cannot become a face, or '' if it is fine."""
    if not isinstance(profile, list) or len(profile) < 3:
        return ""  # the type check already reported this
    try:
        points = [(float(x), float(y)) for x, y in profile]
    except (TypeError, ValueError):
        return ""  # ditto

    # Repeating the first point at the end is harmless - the interpreter closes
    # the wire itself - so drop it before looking for genuine duplicates.
    if len(points) > 3 and _same_point(points[0], points[-1]):
        points = points[:-1]

    for i, point in enumerate(points):
        nxt = (i + 1) % len(points)
        if _same_point(point, points[nxt]):
            return (f"points {i + 1} and {nxt + 1} are both {list(point)}. A "
                    "repeated point silently drops a corner - give each corner "
                    "of the outline once, in order.")
    if len(points) < 3:
        return "needs at least 3 distinct points."
    # Self-intersection is tested before the area test, not after: a symmetric
    # bowtie encloses equal and opposite lobes, so its shoelace area is exactly
    # zero and the collinear test would otherwise claim the wrong reason.
    crossed = _crossing(points)
    if crossed:
        return (f"edge {crossed[0]} crosses edge {crossed[1]}. The outline must "
                "be a simple (non-self-intersecting) polygon - list the corners "
                "in order around the perimeter, clockwise or anticlockwise.")
    if abs(_signed_area(points)) <= _AREA_TOL:
        return ("all the points lie on one straight line, so the outline "
                "encloses no area and builds a zero-volume solid.")
    return ""


# --------------------------------------------------------------------------- #
# Program validation
# --------------------------------------------------------------------------- #
def validate_program(data: Any) -> List[Dict[str, Any]]:
    """Validate an IR program and return its list of operations.

    Accepts either ``{"operations": [...]}`` or a bare ``[...]`` list. Raises
    :class:`SchemaError` with a precise message on any problem, including
    references to undefined objects and duplicate names.
    """
    if isinstance(data, dict):
        operations = data.get("operations")
        if operations is None:
            raise SchemaError(
                "Program must have an 'operations' array. "
                f"Got keys: {list(data.keys())}."
            )
    elif isinstance(data, list):
        operations = data
    else:
        raise SchemaError("Program must be a JSON object or array of operations.")

    if not isinstance(operations, list) or not operations:
        raise SchemaError("'operations' must be a non-empty array.")

    defined: set = set()

    for index, op in enumerate(operations):
        loc = f"operations[{index}]"
        spec = _validate_op_fields(op, defined, loc)

        # Register the object this op creates.
        if spec["defines"]:
            new_name = op["name"]
            if new_name in defined:
                raise SchemaError(f"{loc}: object name '{new_name}' is already used.")
            defined.add(new_name)

    return operations


def _validate_op_fields(op: Any, defined: set, loc: str) -> Dict[str, Any]:
    """Validate one op's structure, types, positivity, enums and references
    (against the ``defined`` name set). Does NOT check name uniqueness or register
    the op - callers handle that. Returns the op's spec."""
    if not isinstance(op, dict):
        raise SchemaError(f"{loc} must be an object, got {op!r}.")
    name_op = op.get("op")
    if name_op not in OPERATIONS:
        raise SchemaError(f"{loc}: unknown op '{name_op}'. Valid ops: {', '.join(OPERATIONS)}.")
    spec = OPERATIONS[name_op]
    enums = spec.get("enums", {})

    for field, kind in spec["required"].items():
        if field not in op:
            raise SchemaError(f"{loc} ('{name_op}') is missing required field '{field}'.")
        _check_field(name_op, field, kind, op[field], enums.get(field))

    for field, kind in spec["optional"].items():
        if field in op and op[field] is not None:
            _check_field(name_op, field, kind, op[field], enums.get(field))

    for field in spec.get("positive", []):
        if field in op and _is_number(op[field]) and op[field] <= 0:
            raise SchemaError(f"operation '{name_op}', field '{field}' must be > 0, got {op[field]}.")

    checker = _SEMANTIC.get(name_op)
    if checker is not None:
        checker(op)

    for ref_field in spec["refs"]:
        value = op.get(ref_field)
        names = value if isinstance(value, list) else [value]
        for n in names:
            if n not in defined:
                raise SchemaError(
                    f"{loc} ('{name_op}') references object '{n}' which has not been "
                    f"created yet. Defined so far: {sorted(defined) or 'none'}."
                )
    return spec


def validate_op(op: Dict[str, Any], defined_names=()) -> Dict[str, Any]:
    """Validate a single op against a set of already-defined object names.

    Used by the engineering step form for instant per-field feedback. Skips the
    name-uniqueness check (the timeline controller owns that). Raises SchemaError.
    """
    _validate_op_fields(op, set(defined_names), "operation")
    return op


def leaf_names(operations: List[Dict[str, Any]]) -> List[str]:
    """Objects that no later operation consumes - the program's end products.

    An operation that defines a new object consumes the ones it references: the
    interpreter (or FreeCAD itself, for ``Part::Cut`` and friends) hides them,
    so they stop being visible geometry. ``translate``/``rotate`` only move an
    object and leave it a leaf, as does a pattern with ``keep_source``.

    A single-part program should end with exactly one leaf. Extra leaves are
    solids left floating in the document - real geometry the user did not ask
    for, which post-build inspection of the final object alone cannot see.
    """
    defined: List[str] = []
    consumed: set = set()
    for op in operations or []:
        spec = OPERATIONS.get(op.get("op"))
        if spec is None or not spec["defines"]:
            continue
        for ref_field in spec["refs"]:
            if ref_field == "source" and op.get("keep_source"):
                continue  # deliberately kept visible alongside the pattern
            value = op.get(ref_field)
            for name in (value if isinstance(value, list) else [value]):
                if isinstance(name, str):
                    consumed.add(name)
        if isinstance(op.get("name"), str):
            defined.append(op["name"])
    return [name for name in defined if name not in consumed]


# --------------------------------------------------------------------------- #
# Derived artifacts (prompt reference + JSON schema)
# --------------------------------------------------------------------------- #
def operations_reference() -> str:
    """Human-readable catalogue of ops, injected into the system prompt."""
    lines = []
    for op, spec in OPERATIONS.items():
        req = ", ".join(f"{f}:{k}" for f, k in spec["required"].items())
        opt = ", ".join(f"{f}:{k}" for f, k in spec["optional"].items())
        sig = req + (f" [, {opt}]" if opt else "")
        lines.append(f"- {op}({sig})\n    {spec['doc']}")
    return "\n".join(lines)


_VEC3_SCHEMA = {"type": "array", "items": {"type": "number"},
                "minItems": 3, "maxItems": 3}
_PLACEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "pos": _VEC3_SCHEMA,
        "rotation": {
            "type": "object",
            "required": ["axis", "angle"],
            "properties": {"axis": _VEC3_SCHEMA, "angle": {"type": "number"}},
        },
    },
}


def _field_schema(kind: str, allowed=None) -> Dict[str, Any]:
    """JSON Schema for one IR field type token."""
    if kind == ENUM:
        return {"enum": list(allowed or [])}
    return {
        NUMBER: {"type": "number"},
        INT: {"type": "integer"},
        BOOL: {"type": "boolean"},
        STRING: {"type": "string", "minLength": 1},
        VEC3: _VEC3_SCHEMA,
        INTLIST: {"type": "array", "items": {"type": "integer"}},
        STRLIST: {"type": "array", "items": {"type": "string"}, "minItems": 2},
        PROFILE: {
            "type": "array",
            "minItems": 3,
            "items": {"type": "array", "items": {"type": "number"},
                      "minItems": 2, "maxItems": 2},
        },
        PLACEMENT: _PLACEMENT_SCHEMA,
    }.get(kind, {})


def _op_schema(op: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    enums = spec.get("enums", {})
    properties: Dict[str, Any] = {"op": {"const": op}}
    for field, kind in spec["required"].items():
        properties[field] = _field_schema(kind, enums.get(field))
    for field, kind in spec["optional"].items():
        properties[field] = _field_schema(kind, enums.get(field))
    return {
        "type": "object",
        "required": ["op", *spec["required"].keys()],
        "properties": properties,
        # Left permissive to match validate_program, which ignores unknown keys.
        "additionalProperties": True,
    }


def json_schema() -> Dict[str, Any]:
    """A JSON schema describing a valid program.

    One branch per operation, carrying that op's *required* fields - so a
    provider that enforces the schema (a local model, where it is compiled to a
    GBNF grammar) cannot emit an op with its dimensions missing. An earlier
    version only constrained ``op`` itself, which let a grammar-constrained model
    return ``{"op": "box"}`` with no name or size: accepted by the grammar, then
    rejected by :func:`validate_program`. Derived from :data:`OPERATIONS` so the
    schema, the validator and the prompt reference cannot drift apart.
    """
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "GPT4FreeCAD program",
        "type": "object",
        "required": ["operations"],
        "properties": {
            "operations": {
                "type": "array",
                "minItems": 1,
                "items": {"anyOf": [_op_schema(op, spec)
                                    for op, spec in OPERATIONS.items()]},
            }
        },
    }


def example_program() -> Dict[str, Any]:
    """A small worked example used in the prompt and tests."""
    return {
        "operations": [
            {"op": "box", "name": "plate", "length": 40, "width": 40, "height": 10},
            {"op": "cylinder", "name": "bore", "radius": 6, "height": 12,
             "placement": {"pos": [20, 20, -1]}},
            {"op": "cut", "name": "result", "base": "plate", "tool": "bore"},
            {"op": "fillet", "name": "finished", "target": "result", "radius": 2},
        ]
    }
