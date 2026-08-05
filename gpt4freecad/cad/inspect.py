"""Post-build geometric inspection of built results.

After the interpreter builds a program, the *shape* can still be defective even
though every operation succeeded: a cutting tool that missed its target leaves
the base untouched, a boolean can yield a zero-volume or open shell, a compound
can hide disconnected lumps. ``inspect_object`` gathers plain facts from a built
FreeCAD object (duck-typed - no FreeCAD import needed) and the pure helpers
``problems``/``summary`` turn those facts into warnings and a one-line report,
so the whole module is unit-testable without FreeCAD.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

# Below this (mm^3) a "solid" is effectively empty.
_MIN_VOLUME = 1e-6


def inspect_object(obj) -> Dict[str, Any]:
    """Collect geometric facts about a built object's shape.

    Never raises for missing attributes; each fact degrades to its pessimistic
    default so ``problems`` can still report something useful.
    """
    name = getattr(obj, "Label", None) or getattr(obj, "Name", None) or "result"
    facts: Dict[str, Any] = {"name": str(name), "null": False, "valid": False,
                             "solids": 0, "closed": False, "volume": 0.0,
                             "bbox": [0.0, 0.0, 0.0]}
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        try:  # primitives have no Shape until a recompute
            obj.Document.recompute()
            shape = obj.Shape
        except Exception:
            shape = None
    if shape is None or shape.isNull():
        facts["null"] = True
        return facts

    try:
        facts["valid"] = bool(shape.isValid())
    except Exception:
        pass
    solids = list(getattr(shape, "Solids", None) or [])
    facts["solids"] = len(solids)
    try:
        facts["closed"] = bool(solids) and all(
            sh.isClosed() for s in solids for sh in (s.Shells or []))
    except Exception:
        pass
    try:
        facts["volume"] = float(shape.Volume)
    except Exception:
        pass
    try:
        bb = shape.BoundBox
        facts["bbox"] = [float(bb.XLength), float(bb.YLength), float(bb.ZLength)]
    except Exception:
        pass
    return facts


def problems(facts: Dict[str, Any], expect_single: bool = False) -> List[str]:
    """Human/model-readable defects in ``facts``; empty list means healthy.

    ``expect_single`` adds the fused-layout expectation that the result is one
    connected solid (separate layouts legitimately produce several).
    """
    if facts.get("null"):
        return ["produced no geometry (null shape)"]
    out: List[str] = []
    if not facts.get("valid", False):
        out.append("shape failed the geometry kernel's validity check")
    solids = int(facts.get("solids", 0) or 0)
    if solids == 0:
        out.append("result contains no solid (only faces/edges)")
    elif not facts.get("closed", False):
        out.append("result is not watertight (open shell)")
    if expect_single and solids > 1:
        out.append(f"result is {solids} disconnected solids; expected one fused part")
    if float(facts.get("volume", 0.0) or 0.0) <= _MIN_VOLUME:
        out.append("result has zero (or negative) volume - a boolean cut/common "
                   "probably missed its target")
    return out


def summary(facts: Dict[str, Any]) -> str:
    """One-line inspection report for the activity log."""
    name = facts.get("name", "result")
    if facts.get("null"):
        return f"Inspection '{name}': no geometry."
    x, y, z = (list(facts.get("bbox") or []) + [0.0, 0.0, 0.0])[:3]
    return (f"Inspection '{name}': {int(facts.get('solids', 0) or 0)} solid(s), "
            f"volume {float(facts.get('volume', 0.0) or 0.0):.1f} mm³, "
            f"bbox {x:.1f}×{y:.1f}×{z:.1f} mm.")


# --------------------------------------------------------------------------- #
# Whole-program review
#
# Inspecting only the object the last operation produced misses everything the
# program left beside it. A plan that builds a plate, builds a rib it then never
# references, and fillets the plate finishes with a healthy filleted plate - and
# a loose rib sitting in the document that no check ever looked at. The leaf
# names come from schema.leaf_names(); this side measures them.
# --------------------------------------------------------------------------- #
def inspect_leaves(objects: Dict[str, Any], names: Sequence[str]) -> List[Dict[str, Any]]:
    """Facts for each end-product object, in program order.

    Falls back to inspecting nothing for a name with no object (an op that
    defines no object, or one FreeCAD renamed), rather than failing the review.
    """
    out: List[Dict[str, Any]] = []
    for name in names or []:
        obj = (objects or {}).get(name)
        if obj is None:
            continue
        try:
            facts = inspect_object(obj)
        except Exception:
            continue
        facts["ir_name"] = name
        out.append(facts)
    return out


def program_problems(leaves: List[Dict[str, Any]], expect_single: bool = False) -> List[str]:
    """Defects across every end product, each tagged with the object it is in.

    ``expect_single`` (a single-part program) also makes more than one end
    product a defect in itself: those extra solids are geometry the user did
    not ask for.
    """
    out: List[str] = []
    for facts in leaves or []:
        name = facts.get("ir_name") or facts.get("name", "result")
        for problem in problems(facts, expect_single=False):
            out.append(f"'{name}' {problem}")
    if expect_single and len(leaves or []) > 1:
        names = ", ".join(f"'{f.get('ir_name') or f.get('name')}'" for f in leaves)
        out.append(
            f"the program leaves {len(leaves)} separate solids ({names}) but "
            "this is a single part - every feature should end up fused into "
            "one result, or not be built at all"
        )
    return out


def measurement_table(leaves: List[Dict[str, Any]]) -> str:
    """Per-object measurements, for a repair or review prompt.

    A repair round that is told only "the result has zero volume" has to guess
    which operation went wrong. Given the measurements it can see, for example,
    that the tool it cut with is 200 mm from the part it was cutting.
    """
    lines = []
    for facts in leaves or []:
        name = facts.get("ir_name") or facts.get("name", "result")
        if facts.get("null"):
            lines.append(f"- {name}: no geometry")
            continue
        x, y, z = (list(facts.get("bbox") or []) + [0.0, 0.0, 0.0])[:3]
        lines.append(
            f"- {name}: volume {float(facts.get('volume', 0.0) or 0.0):.1f} mm3, "
            f"bounding box {x:.1f} x {y:.1f} x {z:.1f} mm, "
            f"{int(facts.get('solids', 0) or 0)} solid(s)"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Dimension verification
# --------------------------------------------------------------------------- #
_TO_MM = {"mm": 1.0, "millimetre": 1.0, "millimeter": 1.0,
          "cm": 10.0, "centimetre": 10.0, "centimeter": 10.0,
          "m": 1000.0, "metre": 1000.0, "meter": 1000.0,
          "in": 25.4, "inch": 25.4, "inches": 25.4, '"': 25.4}

# A number followed by a unit. The unit is required: it is what separates a
# dimension from a quantity, so "4 M3 bolts" and "6 holes" contribute nothing.
_DIM_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(millimet(?:re|er)s?|centimet(?:re|er)s?|met(?:re|er)s?|inch(?:es)?"
    r"|mm|cm|in|m|\")(?![a-z0-9])",
    re.IGNORECASE,
)


def stated_dimensions(text: str) -> List[float]:
    """Every length the request states with an explicit unit, in mm."""
    out = []
    for value, unit in _DIM_RE.findall(text or ""):
        factor = _TO_MM.get(unit.lower().rstrip("s") if unit != '"' else '"')
        if factor is None:
            factor = _TO_MM.get(unit.lower())
        if factor:
            try:
                out.append(float(value) * factor)
            except ValueError:
                pass
    return out


def dimension_check(text: str, leaves: List[Dict[str, Any]]) -> Optional[str]:
    """Warn if the largest length the user asked for is nowhere in the build.

    Only the largest is checked, and only for a single-part result. A
    description mixes overall sizes with hole diameters, wall thicknesses and
    clearances, and only the largest of them is reliably an overall dimension -
    checking every stated number would report a 5 mm hole as a missing 5 mm
    bounding box on every part ever built.
    """
    if not leaves or len(leaves) != 1:
        return None
    stated = stated_dimensions(text)
    if not stated:
        return None
    target = max(stated)
    bbox = [float(v) for v in (leaves[0].get("bbox") or [])]
    if not bbox:
        return None
    tolerance = max(0.5, target * 0.02)
    if any(abs(axis - target) <= tolerance for axis in bbox):
        return None
    built = " x ".join(f"{v:.1f}" for v in bbox)
    return (f"the request asks for {target:g} mm, but the part measures "
            f"{built} mm - no dimension matches")
