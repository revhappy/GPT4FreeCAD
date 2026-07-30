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


# Notes emitted by handlers when they deterministically corrected something
# (clamped a radius, dropped a bad edge index). Drained into the build log
# after each op so the panel can surface them. Builds run on the main thread,
# so a module-level list is safe.
_PENDING_NOTES: List[str] = []


def _note(text: str) -> None:
    _PENDING_NOTES.append(text)


def _drain_notes() -> List[str]:
    notes, _PENDING_NOTES[:] = list(_PENDING_NOTES), []
    return notes


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
    for index, op in enumerate(operations, start=1):
        try:
            obj = _dispatch(op, doc, objects)
        except Exception as exc:  # noqa: BLE001 - re-raise with op context
            raise InterpreterError(_op_error(index, op, objects, exc)) from exc
        log.extend(f"note: {n}" for n in _drain_notes())
        if obj is not None:
            objects[op["name"]] = obj
            result = obj
            log.append(f"{op['op']} -> {obj.Name}")
        else:
            log.append(f"{op['op']} {op.get('target', '')}")
    return result


def _op_error(index, op, objects, exc) -> str:
    """One precise, model-repairable sentence about which op failed and why."""
    label = op.get("name") or op.get("target") or op.get("source") or "?"
    if isinstance(exc, KeyError):
        detail = f"references undefined object {exc}"
    else:
        detail = str(exc)
    built = ", ".join(f"'{n}'" for n in objects) or "none"
    return (f"operation #{index} '{op['op']}' ('{label}') failed: {detail} "
            f"[operations before it succeeded; objects built so far: {built}]")


def _check_built(objects: Dict[str, Any]) -> None:
    """Raise if any built object ended up with a null shape.

    Parametric features (Part::Chamfer, Part::Fillet, ...) only compute on
    doc.recompute(); an OCC failure there leaves a null shape instead of
    raising, which would otherwise commit a silently-broken step.
    """
    for ir_name, obj in objects.items():
        shape = getattr(obj, "Shape", None)
        if shape is not None and shape.isNull():
            kind = getattr(obj, "TypeId", "feature").split("::")[-1]
            raise InterpreterError(
                f"'{ir_name}' produced no geometry (the {kind} failed to "
                "compute). Try a smaller size or different edges."
            )


def _check_booleans(operations, objects: Dict[str, Any]) -> None:
    """Raise if a cut/hole removed no material - a silently-missed boolean.

    A tool that does not intersect its target still 'succeeds' and leaves a
    valid shape, so volume comparison is the only way to catch it.
    """
    for op in operations:
        if op["op"] not in ("cut", "hole"):
            continue
        source_name = op.get("base") or op.get("target")
        result_obj = objects.get(op["name"])
        source_obj = objects.get(source_name)
        if result_obj is None or source_obj is None:
            continue
        try:
            v_result = float(result_obj.Shape.Volume)
            v_source = float(source_obj.Shape.Volume)
        except Exception:
            continue
        if v_source <= 0:
            continue
        if v_result >= v_source - max(1e-6, 1e-9 * v_source):
            raise InterpreterError(
                f"'{op['name']}' ({op['op']}) removed no material from "
                f"'{source_name}' - the tool does not intersect it. Check the "
                "placement and size of the cutting tool."
            )


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
    _drain_notes()
    doc.openTransaction("GPT4FreeCAD")
    try:
        result = _run_ops(operations, doc, objects, log)
        if group_separate:
            _group_visible_components(doc, objects, log)
        doc.recompute()
        _check_built(objects)
        _check_booleans(operations, objects)
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
    _drain_notes()
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
        _check_built(objects)
        _check_booleans(program, objects)
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


def _revolve(op, doc, _objects):
    """Revolve a closed [r, z] profile around the Z axis (lathe-style solid)."""
    for r, _z in op["profile"]:
        if r < 0:
            raise InterpreterError(
                "revolve profile points must have r >= 0 (r is the distance "
                f"from the Z axis); got r={r}. Keep the whole profile on one "
                "side of the axis."
            )
    points = [Vector(r, 0, z) for r, z in op["profile"]]
    points.append(points[0])  # close the wire
    wire = Part.makePolygon(points)
    face = Part.Face(wire)
    angle = op.get("angle") or 360
    solid = face.revolve(Vector(0, 0, 0), Vector(0, 0, 1), angle)
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
def _modifiable_edge_ids(shape):
    """1-based indices of edges fillet/chamfer can act on.

    Cylindrical/conical faces carry a *seam* edge (where the surface wraps
    around) that belongs to only one face; OCC cannot fillet or chamfer those
    and yields a null shape. So "all edges" means every non-degenerate edge
    shared by two distinct faces.
    """
    ids = []
    for i, edge in enumerate(shape.Edges, start=1):
        if edge.Length < 1e-9:
            continue  # degenerate (e.g. cone apex)
        try:
            faces = shape.ancestorsOfType(edge, Part.Face)
        except Exception:
            faces = []
        if len({f.hashCode() for f in faces}) >= 2:
            ids.append(i)
    return ids


def _edge_ids(op, target):
    """1-based edge ids a fillet/chamfer should act on.

    Honours an explicit 'edges' list but drops out-of-range indices (with a
    note) instead of failing the build; falls back to every modifiable edge
    when nothing usable was requested.
    """
    doc = target.Document
    doc.recompute()  # ensure target.Shape exists so we can count edges
    shape = target.Shape
    if shape is None or shape.isNull() or not shape.Edges:
        raise InterpreterError(f"'{op['target']}' has no edges to modify.")
    n_edges = len(shape.Edges)

    if "edges" in op and op["edges"]:
        ids = [int(i) for i in op["edges"] if 1 <= i <= n_edges]
        dropped = sorted(set(op["edges"]) - set(ids))
        if dropped:
            _note(f"'{op['name']}': dropped out-of-range edge index(es) "
                  f"{dropped} ('{op['target']}' has {n_edges} edges).")
        if ids:
            return ids
        _note(f"'{op['name']}': none of the requested edges exist; "
              "applying to all modifiable edges instead.")

    ids = _modifiable_edge_ids(shape)
    if not ids:
        raise InterpreterError(
            f"'{op['target']}' has no edges that can be filleted/chamfered."
        )
    return ids


def _edge_feature(op, doc, objects, type_id, value_key):
    """Build a Part::Fillet/Chamfer, shrinking the value until OCC accepts it.

    OCC rejects a radius/size larger than the local geometry allows by leaving
    a null shape at recompute. Rather than failing the whole build, retry with
    a progressively halved value; only give up when even a tiny value fails.
    """
    target = objects[op["target"]]
    ids = _edge_ids(op, target)
    requested = float(op[value_key])
    kind = type_id.split("::")[-1].lower()

    obj = doc.addObject(type_id, op["name"])
    obj.Base = target
    value = requested
    for _ in range(4):
        obj.Edges = [(i, value, value) for i in ids]
        doc.recompute()
        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull():
            if value != requested:
                _note(f"'{op['name']}': {value_key} {requested:g} was too large "
                      f"for the geometry; used {value:g} instead.")
            _hide(target)
            return obj
        value = round(value / 2.0, 6)
    raise InterpreterError(
        f"'{op['name']}' failed even at {value_key} {value * 2:g} - the edges "
        f"of '{op['target']}' cannot take this {kind}. Use fewer/other edges "
        "or a smaller value."
    )


def _fillet(op, doc, objects):
    return _edge_feature(op, doc, objects, "Part::Fillet", "radius")


def _chamfer(op, doc, objects):
    return _edge_feature(op, doc, objects, "Part::Chamfer", "size")


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
    "revolve": _revolve,
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
