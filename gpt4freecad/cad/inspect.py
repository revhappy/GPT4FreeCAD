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

from typing import Any, Dict, List

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
