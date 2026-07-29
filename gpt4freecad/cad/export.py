"""STL/STEP export + build-volume helpers for 3D-print mode.

``overage`` is pure (no FreeCAD) so it is unit-testable; the geometry helpers
import FreeCAD lazily so this module can still be imported without it.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple


# --------------------------------------------------------------------------- #
# Pure
# --------------------------------------------------------------------------- #
def overage(dims: Sequence[float], bed: Sequence[float]) -> List[float]:
    """Per-axis amount (same units as inputs) by which ``dims`` exceed ``bed``.

    Returns ``[dx, dy, dz]`` where each is ``max(0, dim - bed)``. All zeros means
    the part fits the build volume.
    """
    return [max(0.0, float(d) - float(b)) for d, b in zip(dims, bed)]


def fits(dims: Sequence[float], bed: Sequence[float]) -> bool:
    return not any(overage(dims, bed))


def fit_factor(dims: Sequence[float], bed: Sequence[float]) -> float:
    """Uniform scale factor that makes ``dims`` fit ``bed`` (<=1 means shrink)."""
    factors = [float(b) / float(d) for d, b in zip(dims, bed) if d]
    return min(factors) if factors else 1.0


# --------------------------------------------------------------------------- #
# FreeCAD (lazy imports)
# --------------------------------------------------------------------------- #
def bbox(obj) -> Tuple[float, float, float]:
    """Bounding-box dimensions (mm) of an object's shape."""
    bb = obj.Shape.BoundBox
    return (bb.XLength, bb.YLength, bb.ZLength)


def scale_to_fit(obj, bed: Sequence[float], doc=None):
    """Uniformly scale ``obj`` (about the origin) so it fits ``bed``.

    Returns a new scaled ``Part::Feature`` (originals hidden), or ``obj`` itself
    if it already fits. Scaling about the origin keeps a base on z=0 on the bed.
    """
    import FreeCAD as App

    dims = bbox(obj)
    factor = fit_factor(dims, bed)
    if factor >= 1.0:
        return obj  # already fits
    if doc is None:
        doc = obj.Document
    matrix = App.Matrix()
    matrix.scale(factor, factor, factor)
    scaled = obj.Shape.transformGeometry(matrix)
    new = doc.addObject("Part::Feature", obj.Name + "_scaled")
    new.Shape = scaled
    try:
        obj.ViewObject.Visibility = False
    except Exception:
        pass
    doc.recompute()
    return new


def export_step(obj, path: str) -> str:
    """Write ``obj`` to a STEP file (exact B-rep, not a mesh). Returns the path.

    STEP preserves the exact geometry for downstream CAD/CAM interchange.
    """
    import Part

    objs = [obj]
    if not hasattr(obj, "Shape") and hasattr(obj, "Group"):
        # An App::Part assembly container: export its geometric children.
        objs = [o for o in obj.Group if hasattr(o, "Shape")]
    Part.export(objs, path)
    return path


def export_stl(obj, path: str, linear_deflection: float = 0.1,
               angular_deflection: float = 0.5) -> str:
    """Tessellate ``obj`` and write an STL to ``path``. Returns the path.

    Prefers MeshPart for deflection control; falls back to Part's exportStl.
    """
    try:
        import MeshPart

        mesh = MeshPart.meshFromShape(
            Shape=obj.Shape,
            LinearDeflection=linear_deflection,
            AngularDeflection=angular_deflection,
            Relative=False,
        )
        mesh.write(path)
    except Exception:
        # Fallback: Part's own tessellated STL export.
        obj.Shape.exportStl(path)
    return path
