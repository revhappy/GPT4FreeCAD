"""Execute a validated IR program by building parametric FreeCAD objects.

This is the only CAD module that imports FreeCAD. Each operation maps to a
native parametric object (``Part::Box``, ``Part::Cut``, ``Part::Fillet`` ...) so
the result is a fully editable feature tree, not a dead lump of geometry. The
whole program runs inside a single undo transaction.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import FreeCAD as App
import Part
from FreeCAD import Base, Vector

from . import schema


class InterpreterError(Exception):
    """Raised when a (valid) program cannot be realised as geometry."""


def _placement(spec: Optional[dict]) -> App.Placement:
    """Build an App.Placement from an IR placement dict (pos + rotation)."""
    if not spec:
        return App.Placement()
    pos = Vector(*spec["pos"]) if "pos" in spec else Vector(0, 0, 0)
    if "rotation" in spec:
        rot = spec["rotation"]
        rotation = App.Rotation(Vector(*rot["axis"]), rot["angle"])
    else:
        rotation = App.Rotation()
    return App.Placement(pos, rotation)


def _run_ops(operations, doc, objects: Dict[str, Any], log: List[str]):
    """Dispatch each op in order, recording created objects. No transaction here."""
    result = None
    for op in operations:
        obj = _dispatch(op, doc, objects)
        if obj is not None:
            objects[op["name"]] = obj
            result = obj
            log.append(f"{op['op']} -> {obj.Name}")
        else:
            log.append(f"{op['op']} {op.get('target', '')}")
    return result


def build_program(operations: List[Dict[str, Any]], doc=None,
                  group_separate: bool = False) -> Tuple[Any, List[str]]:
    """Build ``operations`` into ``doc`` (active document if None).

    Returns ``(result_object, log_lines)``. Validates first. Runs inside one undo
    transaction so a single Undo reverts the whole program.
    """
    operations = schema.validate_program({"operations": operations})
    if doc is None:
        doc = App.ActiveDocument or App.newDocument("Unnamed")

    objects: Dict[str, Any] = {}
    log: List[str] = []
    doc.openTransaction("GPT4FreeCAD")
    try:
        result = _run_ops(operations, doc, objects, log)
        if group_separate:
            _group_visible_components(doc, objects, log)
        doc.recompute()
        doc.commitTransaction()
    except Exception as exc:  # noqa: BLE001 - report any build failure cleanly
        doc.abortTransaction()
        raise InterpreterError(str(exc)) from exc
    return result, log


def rebuild(program: List[Dict[str, Any]], doc=None,
            prior_names: Optional[List[str]] = None,
            group_separate: bool = False) -> Tuple[Any, Dict[str, Any], List[str]]:
    """Deterministically (re)build a whole program for the engineering timeline.

    Removes ``prior_names`` (objects from the previous build, in reverse creation
    order) then replays ``program`` from scratch. Returns ``(result, objects,
    log)`` where ``objects`` maps each IR name to its FreeCAD object so the caller
    can track what to remove next time.
    """
    program = schema.validate_program({"operations": program})
    if doc is None:
        doc = App.ActiveDocument or App.newDocument("Unnamed")

    objects: Dict[str, Any] = {}
    log: List[str] = []
    doc.openTransaction("GPT4FreeCAD rebuild")
    try:
        for name in reversed(list(prior_names or [])):
            try:
                if doc.getObject(name) is not None:
                    doc.removeObject(name)
            except Exception:
                pass  # already gone / consumed by a parent
        result = _run_ops(program, doc, objects, log)
        if group_separate:
            group = _group_visible_components(doc, objects, log)
            if group is not None:
                objects["__assembly__"] = group
        doc.recompute()
        doc.commitTransaction()
    except Exception as exc:  # noqa: BLE001
        doc.abortTransaction()
        raise InterpreterError(str(exc)) from exc
    return result, objects, log


def _group_visible_components(doc, objects, log):
    """Place independent visible solids in an App::Part without fusing them."""
    components = []
    seen = set()
    for obj in objects.values():
        name = getattr(obj, "Name", None)
        if not name or name in seen or not hasattr(obj, "Shape"):
            continue
        seen.add(name)
        try:
            if not obj.ViewObject.Visibility:
                continue
        except Exception:
            pass
        components.append(obj)
    if len(components) < 2:
        return None

    group = doc.addObject("App::Part", "GPT4FreeCAD_Assembly")
    group.Label = "GPT4FreeCAD Assembly"
    for component in components:
        group.addObject(component)
    log.append(f"assembly -> {group.Name} ({len(components)} components)")
    return group


def _dispatch(op: Dict[str, Any], doc, objects: Dict[str, Any]):
    handler = _HANDLERS.get(op["op"])
    if handler is None:  # pragma: no cover - validate_program guards this
        raise InterpreterError(f"No handler for op '{op['op']}'.")
    return handler(op, doc, objects)


# --------------------------------------------------------------------------- #
# Primitive handlers
# --------------------------------------------------------------------------- #
def _box(op, doc, _objects):
    obj = doc.addObject("Part::Box", op["name"])
    obj.Length, obj.Width, obj.Height = op["length"], op["width"], op["height"]
    obj.Placement = _placement(op.get("placement"))
    return obj


def _cylinder(op, doc, _objects):
    obj = doc.addObject("Part::Cylinder", op["name"])
    obj.Radius, obj.Height = op["radius"], op["height"]
    if "angle" in op and op["angle"] is not None:
        obj.Angle = op["angle"]
    obj.Placement = _placement(op.get("placement"))
    return obj


def _sphere(op, doc, _objects):
    obj = doc.addObject("Part::Sphere", op["name"])
    obj.Radius = op["radius"]
    obj.Placement = _placement(op.get("placement"))
    return obj


def _cone(op, doc, _objects):
    obj = doc.addObject("Part::Cone", op["name"])
    obj.Radius1, obj.Radius2, obj.Height = op["radius1"], op["radius2"], op["height"]
    obj.Placement = _placement(op.get("placement"))
    return obj


def _torus(op, doc, _objects):
    obj = doc.addObject("Part::Torus", op["name"])
    obj.Radius1, obj.Radius2 = op["radius1"], op["radius2"]
    obj.Placement = _placement(op.get("placement"))
    return obj


def _extrude(op, doc, _objects):
    points = [Vector(x, y, 0) for x, y in op["profile"]]
    points.append(points[0])  # close the wire
    wire = Part.makePolygon(points)
    face = Part.Face(wire)
    solid = face.extrude(Vector(0, 0, op["height"]))
    obj = doc.addObject("Part::Feature", op["name"])
    obj.Shape = solid
    obj.Placement = _placement(op.get("placement"))
    return obj


# --------------------------------------------------------------------------- #
# Boolean handlers
# --------------------------------------------------------------------------- #
def _cut(op, doc, objects):
    obj = doc.addObject("Part::Cut", op["name"])
    obj.Base = objects[op["base"]]
    obj.Tool = objects[op["tool"]]
    return obj


def _multi(kind):
    def handler(op, doc, objects):
        obj = doc.addObject(kind, op["name"])
        obj.Shapes = [objects[n] for n in op["parts"]]
        return obj
    return handler


# --------------------------------------------------------------------------- #
# Modifier handlers
# --------------------------------------------------------------------------- #
def _edge_list(op, target, value_keys):
    """Return Part::Fillet/Chamfer edge tuples.

    ``value_keys`` is ('radius',) or ('size',); the value is used for both ends.
    Honours an explicit 1-based 'edges' list, else applies to every edge.
    """
    doc = target.Document
    doc.recompute()  # ensure target.Shape exists so we can count edges
    shape = target.Shape
    if shape is None or shape.isNull() or not shape.Edges:
        raise InterpreterError(f"'{op['target']}' has no edges to modify.")
    n_edges = len(shape.Edges)
    value = op[value_keys[0]]

    if "edges" in op and op["edges"]:
        ids = op["edges"]
        for i in ids:
            if i < 1 or i > n_edges:
                raise InterpreterError(
                    f"edge index {i} out of range for '{op['target']}' "
                    f"(has {n_edges} edges)."
                )
    else:
        ids = range(1, n_edges + 1)
    return [(int(i), float(value), float(value)) for i in ids]


def _fillet(op, doc, objects):
    target = objects[op["target"]]
    edges = _edge_list(op, target, ("radius",))
    obj = doc.addObject("Part::Fillet", op["name"])
    obj.Base = target
    obj.Edges = edges
    _hide(target)
    return obj


def _chamfer(op, doc, objects):
    target = objects[op["target"]]
    edges = _edge_list(op, target, ("size",))
    obj = doc.addObject("Part::Chamfer", op["name"])
    obj.Base = target
    obj.Edges = edges
    _hide(target)
    return obj


def _translate(op, _doc, objects):
    obj = objects[op["target"]]
    delta = Vector(*op["vector"])
    pl = obj.Placement
    obj.Placement = App.Placement(pl.Base + delta, pl.Rotation)
    return None  # mutates in place, defines no new object


def _rotate(op, _doc, objects):
    obj = objects[op["target"]]
    axis = Vector(*op["axis"])
    center = Vector(*op["center"]) if "center" in op and op["center"] else Vector(0, 0, 0)
    rot = App.Placement(Vector(0, 0, 0), App.Rotation(axis, op["angle"]), center)
    obj.Placement = rot.multiply(obj.Placement)
    return None


def _hide(obj):
    try:
        if obj.ViewObject is not None:
            obj.ViewObject.Visibility = False
    except Exception:
        pass  # headless / no GUI - harmless


# --------------------------------------------------------------------------- #
# Engineering handlers (patterns / mirror / shell / hole)
#
# These read a source object's *shape* to build new geometry, so they recompute
# the document first (parametric primitives have no Shape until recompute).
# --------------------------------------------------------------------------- #
_PLANE_NORMALS = {"XY": (0, 0, 1), "XZ": (0, 1, 0), "YZ": (1, 0, 0)}


def _computed_shape(obj):
    obj.Document.recompute()
    shape = obj.Shape
    if shape is None or shape.isNull():
        raise InterpreterError(f"'{obj.Name}' has no shape to work from yet.")
    return shape


def _feature(doc, name, shape):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    return obj


def _combine(shapes, fuse):
    if len(shapes) == 1:
        return shapes[0]
    if fuse:
        return shapes[0].multiFuse(shapes[1:])
    return Part.makeCompound(shapes)


def _oriented(shape, axis_n, origin):
    """Place a +Z-grown tool shape so its z=0 end sits at ``origin`` and it
    extends along ``axis_n``."""
    out = shape.copy()
    out.Placement = App.Placement(origin, App.Rotation(Vector(0, 0, 1), axis_n))
    return out


def _linear_pattern(op, doc, objects):
    base = _computed_shape(objects[op["source"]])
    d1 = Vector(*op["direction"])
    if d1.Length == 0:
        raise InterpreterError("linear_pattern 'direction' must be non-zero.")
    d1.normalize()
    n1, s1 = int(op["count"]), op["spacing"]

    d2 = Vector(*op.get("direction2", [0, 0, 0]))
    n2 = int(op.get("count2", 1) or 1)
    s2 = op.get("spacing2", 0) or 0
    if d2.Length:
        d2.normalize()

    copies = []
    for i in range(n1):
        for j in range(max(n2, 1)):
            c = base.copy()
            c.translate(d1 * (i * s1) + d2 * (j * s2))
            copies.append(c)
    obj = _feature(doc, op["name"], _combine(copies, op.get("fuse", True)))
    if not op.get("keep_source", False):
        _hide(objects[op["source"]])
    return obj


def _polar_pattern(op, doc, objects):
    base = _computed_shape(objects[op["source"]])
    count = max(int(op["count"]), 1)
    angle = op.get("angle", 360)
    axis = Vector(*op.get("axis", [0, 0, 1]))
    if axis.Length == 0:
        raise InterpreterError("polar_pattern 'axis' must be non-zero.")
    center = Vector(*op.get("center", [0, 0, 0]))

    full = abs(angle) >= 360 - 1e-9
    if full:
        step = 360.0 / count
    else:
        step = angle / (count - 1) if count > 1 else 0.0

    copies = []
    for i in range(count):
        c = base.copy()
        c.rotate(center, axis, step * i)
        copies.append(c)
    obj = _feature(doc, op["name"], _combine(copies, op.get("fuse", True)))
    if not op.get("keep_source", False):
        _hide(objects[op["source"]])
    return obj


def _mirror(op, doc, objects):
    src = objects[op["source"]]
    base = _computed_shape(src)
    normal = Vector(*_PLANE_NORMALS[str(op["plane"]).upper()])
    point = Vector(*op.get("base", [0, 0, 0]))
    mirrored = base.mirror(point, normal)
    combine = op.get("combine", True)
    shape = base.fuse(mirrored) if combine else mirrored
    obj = _feature(doc, op["name"], shape)
    if combine:
        _hide(src)
    return obj


def _shell(op, doc, objects):
    src = objects[op["source"]]
    base = _computed_shape(src)
    faces = base.Faces
    if not faces:
        raise InterpreterError("shell: source has no faces.")
    idx = op.get("open_faces")
    if idx:
        chosen = []
        for i in idx:
            if i < 1 or i > len(faces):
                raise InterpreterError(
                    f"shell open_faces index {i} out of range (1..{len(faces)})."
                )
            chosen.append(faces[i - 1])
    else:
        chosen = [max(faces, key=lambda f: f.CenterOfMass.z)]  # default: open the top
    shape = base.makeThickness(chosen, -abs(op["thickness"]), 1e-3)
    obj = _feature(doc, op["name"], shape)
    _hide(src)
    return obj


def _hole(op, doc, objects):
    target = objects[op["target"]]
    base = _computed_shape(target)
    pos = Vector(*op["position"])
    axis = Vector(*op.get("axis", [0, 0, -1]))
    if axis.Length == 0:
        axis = Vector(0, 0, -1)
    axis.normalize()

    eps = 0.01
    start = pos - axis * eps          # start just above the surface for clean cuts
    radius = op["diameter"] / 2.0
    depth = op["depth"]
    if op.get("through"):
        depth = base.BoundBox.DiagonalLength + 2.0

    tools = [_oriented(Part.makeCylinder(radius, depth + 2 * eps), axis, start)]

    if op.get("cbore_diameter") and op.get("cbore_depth"):
        tools.append(_oriented(
            Part.makeCylinder(op["cbore_diameter"] / 2.0, op["cbore_depth"] + eps),
            axis, start))
    if op.get("csink_diameter") and op.get("csink_angle"):
        csd = op["csink_diameter"]
        cdepth = (csd / 2.0) / math.tan(math.radians(op["csink_angle"] / 2.0))
        tools.append(_oriented(Part.makeCone(csd / 2.0, radius, cdepth), axis, start))

    tool = tools[0] if len(tools) == 1 else tools[0].multiFuse(tools[1:])
    obj = _feature(doc, op["name"], base.cut(tool))
    _hide(target)
    return obj


_HANDLERS = {
    "box": _box,
    "cylinder": _cylinder,
    "sphere": _sphere,
    "cone": _cone,
    "torus": _torus,
    "extrude": _extrude,
    "cut": _cut,
    "fuse": _multi("Part::MultiFuse"),
    "common": _multi("Part::MultiCommon"),
    "fillet": _fillet,
    "chamfer": _chamfer,
    "translate": _translate,
    "rotate": _rotate,
    "linear_pattern": _linear_pattern,
    "polar_pattern": _polar_pattern,
    "mirror": _mirror,
    "shell": _shell,
    "hole": _hole,
}
