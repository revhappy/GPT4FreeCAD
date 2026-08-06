"""Tests for the interpreter's non-geometric logic, with FreeCAD stubbed out.

``cad/interpreter.py`` normally cannot be imported without FreeCAD, so the parts
of it that are plain decision-making - which edges to act on, when to shrink a
fillet, whether a boolean actually removed material, what an error says - went
untested while being some of the most consequential code in the addon.

The stubs below stand in for the handful of FreeCAD behaviours those paths
depend on: a document that recomputes, an object with a shape, and a shape that
can report edges and volume. Real geometry is still only exercised inside
FreeCAD; this covers the reasoning around it.

Run with either::

    python tests/test_interpreter_stubbed.py
    pytest tests/test_interpreter_stubbed.py
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --------------------------------------------------------------------------- #
# Minimal FreeCAD stand-ins, installed before importing the interpreter
# --------------------------------------------------------------------------- #
class FakeEdge:
    """An edge only needs a length here: zero-length ones are skipped as
    degenerate (a cone apex), which is part of what is being tested."""

    def __init__(self, length=1.0):
        self.Length = length


class FakeShape:
    def __init__(self, edges=0, volume=0.0, null=False, valid=True,
                 degenerate=0):
        self.Edges = ([FakeEdge(1.0) for _ in range(edges)]
                      + [FakeEdge(0.0) for _ in range(degenerate)])
        self.Volume = volume
        self._null = null
        self._valid = valid
        self.Solids = []
        self.Shells = []

    def isNull(self):
        return self._null

    def isValid(self):
        return self._valid

    def ancestorsOfType(self, _edge, _kind):
        # Two distinct faces per edge, so every edge counts as modifiable.
        return [FakeFace(1), FakeFace(2)]


class FakeFace:
    def __init__(self, code):
        self._code = code

    def hashCode(self):
        return self._code


class FakeViewObject:
    def __init__(self):
        self.Visibility = True


class FakeObject:
    """A document object. ``shapes`` is what Shape becomes on each recompute."""

    def __init__(self, name, document=None, shape=None, shapes=None):
        self.Name = name
        self.Label = name
        self.Document = document
        self.Shape = shape
        self._queue = list(shapes or [])
        self.Edges = None
        self.Base = None
        self.ViewObject = FakeViewObject()
        self.TypeId = "Part::Feature"

    def on_recompute(self):
        if self._queue:
            self.Shape = self._queue.pop(0)


class FakeDocument:
    def __init__(self):
        self.objects = {}
        self.recomputes = 0
        self._pending = []

    def addObject(self, type_id, name):
        obj = FakeObject(name, document=self)
        obj.TypeId = type_id
        self.objects[name] = obj
        self._pending.append(obj)
        return obj

    def recompute(self):
        self.recomputes += 1
        for obj in list(self.objects.values()):
            obj.on_recompute()

    def openTransaction(self, _name):
        pass

    def commitTransaction(self):
        pass

    def abortTransaction(self):
        pass


def _install_stubs():
    freecad = types.ModuleType("FreeCAD")

    class Vector:
        def __init__(self, *args):
            self.args = args

    class Placement:
        def __init__(self, *args):
            self.args = args

    class Rotation:
        def __init__(self, *args):
            self.args = args

    freecad.Vector = Vector
    freecad.Placement = Placement
    freecad.Rotation = Rotation
    freecad.Base = types.ModuleType("FreeCAD.Base")
    freecad.ActiveDocument = None
    freecad.newDocument = lambda _name: FakeDocument()

    part = types.ModuleType("Part")

    class FakeFace:
        """Records the revolve call so tests can assert axis and angle."""

        def __init__(self, wire):
            self.wire = wire

        def revolve(self, base, axis, angle):
            shape = FakeShape(volume=1.0)
            shape.revolve_angle = angle
            return shape

        def extrude(self, direction):
            return FakeShape(volume=1.0)

    part.Face = FakeFace
    part.Feature = object
    part.makePolygon = lambda points: list(points)

    sys.modules.setdefault("FreeCAD", freecad)
    sys.modules.setdefault("Part", part)


_install_stubs()

from gpt4freecad.cad import interpreter  # noqa: E402


def expect_error(fn, exc=Exception):
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__} but none was raised")


# --------------------------------------------------------------------------- #
# notes (deterministic corrections surfaced to the user)
# --------------------------------------------------------------------------- #
def test_notes_drain_once_and_clear():
    interpreter._drain_notes()  # start clean
    interpreter._note("first")
    interpreter._note("second")
    assert interpreter._drain_notes() == ["first", "second"]
    assert interpreter._drain_notes() == []


# --------------------------------------------------------------------------- #
# error context - the thing that makes auto-repair work
# --------------------------------------------------------------------------- #
def test_op_error_names_the_operation_and_what_was_built():
    op = {"op": "fillet", "name": "rounded", "target": "body", "radius": 5}
    message = interpreter._op_error(4, op, {"base": 1, "boss": 2}, ValueError("boom"))
    assert "operation #4" in message
    assert "'fillet'" in message and "'rounded'" in message
    assert "boom" in message
    assert "'base'" in message and "'boss'" in message


def test_op_error_explains_a_missing_reference():
    op = {"op": "cut", "name": "r", "base": "plate", "tool": "ghost"}
    message = interpreter._op_error(2, op, {}, KeyError("ghost"))
    assert "references undefined object" in message
    assert "ghost" in message
    assert "none" in message  # nothing built yet


# --------------------------------------------------------------------------- #
# edge selection
# --------------------------------------------------------------------------- #
def _target_with_edges(count):
    doc = FakeDocument()
    obj = FakeObject("body", document=doc, shape=FakeShape(edges=count, volume=10.0))
    doc.objects["body"] = obj
    return doc, obj


def test_explicit_edges_are_honoured():
    interpreter._drain_notes()
    _doc, target = _target_with_edges(12)
    op = {"op": "fillet", "name": "f", "target": "body", "radius": 1, "edges": [1, 4, 7]}
    assert interpreter._edge_ids(op, target) == [1, 4, 7]
    assert interpreter._drain_notes() == []  # nothing to report


def test_out_of_range_edges_are_dropped_with_a_note_not_an_error():
    """A model guessing edge indices should not kill the whole build."""
    interpreter._drain_notes()
    _doc, target = _target_with_edges(6)
    op = {"op": "fillet", "name": "f", "target": "body", "radius": 1,
          "edges": [2, 99, 100]}
    assert interpreter._edge_ids(op, target) == [2]
    notes = interpreter._drain_notes()
    assert len(notes) == 1
    assert "99" in notes[0] and "100" in notes[0] and "6 edges" in notes[0]


def test_all_edges_bogus_falls_back_to_every_modifiable_edge():
    interpreter._drain_notes()
    _doc, target = _target_with_edges(3)
    op = {"op": "fillet", "name": "f", "target": "body", "radius": 1,
          "edges": [50, 60]}
    assert interpreter._edge_ids(op, target) == [1, 2, 3]
    notes = interpreter._drain_notes()
    assert any("none of the requested edges exist" in n for n in notes)


def test_degenerate_edges_are_excluded_from_all_edges():
    """The seam/apex exclusion from 2.3.1 must survive this rewrite."""
    interpreter._drain_notes()
    doc = FakeDocument()
    target = FakeObject("cone", document=doc,
                        shape=FakeShape(edges=2, degenerate=2, volume=5.0))
    doc.objects["cone"] = target
    op = {"op": "fillet", "name": "f", "target": "cone", "radius": 1}
    # Four edges exist; the two zero-length ones must not be offered to OCC.
    assert interpreter._edge_ids(op, target) == [1, 2]


def test_a_target_with_no_edges_is_a_clear_error():
    doc = FakeDocument()
    target = FakeObject("sphere", document=doc, shape=FakeShape(edges=0))
    op = {"op": "fillet", "name": "f", "target": "sphere", "radius": 1}
    expect_error(lambda: interpreter._edge_ids(op, target), interpreter.InterpreterError)


# --------------------------------------------------------------------------- #
# fillet / chamfer value shrinking
# --------------------------------------------------------------------------- #
def _fillet_setup(successful_attempt, edges=4):
    """A doc whose new feature only produces a shape on ``successful_attempt``."""
    doc = FakeDocument()
    target = FakeObject("body", document=doc, shape=FakeShape(edges=edges, volume=10.0))
    doc.objects["body"] = target

    real_add = doc.addObject

    def add(type_id, name):
        obj = real_add(type_id, name)
        attempts = {"n": 0}

        def on_recompute():
            attempts["n"] += 1
            obj.Shape = (FakeShape(edges=edges, volume=9.0)
                         if attempts["n"] >= successful_attempt
                         else FakeShape(null=True))

        obj.on_recompute = on_recompute
        return obj

    doc.addObject = add
    return doc, target


def test_a_fillet_that_fits_is_used_as_asked_with_no_note():
    interpreter._drain_notes()
    doc, target = _fillet_setup(successful_attempt=1)
    op = {"op": "fillet", "name": "rounded", "target": "body", "radius": 2}
    obj = interpreter._edge_feature(op, doc, {"body": target}, "Part::Fillet", "radius")
    assert obj.Edges == [(1, 2.0, 2.0), (2, 2.0, 2.0), (3, 2.0, 2.0), (4, 2.0, 2.0)]
    assert interpreter._drain_notes() == []
    assert target.ViewObject.Visibility is False  # base hidden behind the feature


def test_an_oversized_fillet_is_halved_until_it_fits_and_says_so():
    """The 2.3.1 fix made an oversized fillet fail the build; now it adapts."""
    interpreter._drain_notes()
    doc, target = _fillet_setup(successful_attempt=3)
    op = {"op": "fillet", "name": "rounded", "target": "body", "radius": 8}
    obj = interpreter._edge_feature(op, doc, {"body": target}, "Part::Fillet", "radius")
    # 8 -> 4 -> 2: the third attempt is the one that computes.
    assert obj.Edges[0] == (1, 2.0, 2.0)
    notes = interpreter._drain_notes()
    assert len(notes) == 1
    assert "8 was too large" in notes[0] and "used 2" in notes[0]


def test_a_fillet_that_never_fits_raises_something_actionable():
    interpreter._drain_notes()
    doc, target = _fillet_setup(successful_attempt=99)
    op = {"op": "chamfer", "name": "beveled", "target": "body", "size": 3}
    try:
        interpreter._edge_feature(op, doc, {"body": target}, "Part::Chamfer", "size")
    except interpreter.InterpreterError as exc:
        assert "beveled" in str(exc)
        assert "cannot take this chamfer" in str(exc)
        assert "smaller value" in str(exc)
    else:
        raise AssertionError("expected InterpreterError")


# --------------------------------------------------------------------------- #
# booleans that quietly removed nothing
# --------------------------------------------------------------------------- #
def test_a_cut_that_removes_material_passes():
    objects = {
        "plate": FakeObject("plate", shape=FakeShape(volume=100.0)),
        "result": FakeObject("result", shape=FakeShape(volume=80.0)),
    }
    ops = [{"op": "cut", "name": "result", "base": "plate", "tool": "bore"}]
    interpreter._check_booleans(ops, objects)  # must not raise


def test_a_cut_whose_tool_missed_is_caught():
    """The classic silent failure: valid shape, untouched volume."""
    objects = {
        "plate": FakeObject("plate", shape=FakeShape(volume=100.0)),
        "result": FakeObject("result", shape=FakeShape(volume=100.0)),
    }
    ops = [{"op": "cut", "name": "result", "base": "plate", "tool": "bore"}]
    try:
        interpreter._check_booleans(ops, objects)
    except interpreter.InterpreterError as exc:
        assert "removed no material" in str(exc)
        assert "does not intersect" in str(exc)
    else:
        raise AssertionError("a missed cut must be reported")


def test_a_hole_that_removed_nothing_is_caught_too():
    objects = {
        "block": FakeObject("block", shape=FakeShape(volume=50.0)),
        "drilled": FakeObject("drilled", shape=FakeShape(volume=50.0)),
    }
    ops = [{"op": "hole", "name": "drilled", "target": "block", "diameter": 3,
            "depth": 5, "position": [0, 0, 0]}]
    expect_error(lambda: interpreter._check_booleans(ops, objects),
                 interpreter.InterpreterError)


def test_non_boolean_ops_and_missing_objects_are_ignored():
    """The check must never invent a failure from incomplete bookkeeping."""
    interpreter._check_booleans([{"op": "box", "name": "b"}], {})
    interpreter._check_booleans(
        [{"op": "cut", "name": "gone", "base": "absent", "tool": "t"}], {})


def test_a_zero_volume_base_is_not_treated_as_a_missed_cut():
    """Dividing attention by zero: a base with no volume proves nothing."""
    objects = {
        "flat": FakeObject("flat", shape=FakeShape(volume=0.0)),
        "result": FakeObject("result", shape=FakeShape(volume=0.0)),
    }
    interpreter._check_booleans(
        [{"op": "cut", "name": "result", "base": "flat", "tool": "t"}], objects)


# --------------------------------------------------------------------------- #
# null-shape guard
# --------------------------------------------------------------------------- #
def test_a_null_shape_after_recompute_fails_the_build():
    objects = {"chamfered": FakeObject("chamfered", shape=FakeShape(null=True))}
    objects["chamfered"].TypeId = "Part::Chamfer"
    try:
        interpreter._check_built([], objects)
    except interpreter.InterpreterError as exc:
        assert "chamfered" in str(exc) and "no geometry" in str(exc)
        assert "Chamfer" in str(exc)
    else:
        raise AssertionError("a null shape must fail the build")


def test_healthy_shapes_pass_the_build_check():
    interpreter._check_built([], {"ok": FakeObject("ok", shape=FakeShape(volume=1.0))})


def test_the_build_check_names_the_operation_that_failed():
    """The op kind comes from the program, not the FreeCAD TypeId, so the
    message speaks the model's vocabulary ('cut', not 'Part::Cut')."""
    ops = [{"op": "cut", "name": "pocket", "base": "a", "tool": "b"}]
    objects = {"pocket": FakeObject("pocket", shape=FakeShape(volume=0.0))}
    try:
        interpreter._check_built(ops, objects)
    except interpreter.InterpreterError as exc:
        assert "'pocket' (cut)" in str(exc) and "no volume" in str(exc)
    else:
        raise AssertionError("a zero-volume result must fail the build")


def test_an_invalid_shape_fails_the_build_even_though_it_is_not_null():
    """The case that motivated the check: OCC returns a shape, not an error."""
    objects = {"rounded": FakeObject("rounded", shape=FakeShape(volume=5.0, valid=False))}
    try:
        interpreter._check_built([], objects)
    except interpreter.InterpreterError as exc:
        assert "rounded" in str(exc) and "invalid" in str(exc)
    else:
        raise AssertionError("an invalid shape must fail the build")


def test_a_negative_volume_shape_fails_the_build():
    """A fillet larger than its edges yields a self-intersecting solid whose
    volume is negative - measured at -21025 mm3 from a 1000 mm3 box."""
    objects = {"rounded": FakeObject("rounded", shape=FakeShape(volume=-21025.4))}
    try:
        interpreter._check_built([], objects)
    except interpreter.InterpreterError as exc:
        assert "self-intersecting" in str(exc) and "negative volume" in str(exc)
    else:
        raise AssertionError("a negative-volume shape must fail the build")


def test_an_oversized_fillet_keeps_shrinking_past_an_invalid_shape():
    """Shrinking used to stop at the first non-null shape, which is how a
    -21025 mm3 result got committed as a finished part."""
    interpreter._drain_notes()
    doc = FakeDocument()
    target = FakeObject("body", document=doc, shape=FakeShape(edges=4, volume=10.0))
    doc.objects["body"] = target
    real_add = doc.addObject

    def add(type_id, name):
        obj = real_add(type_id, name)
        attempts = {"n": 0}

        def on_recompute():
            attempts["n"] += 1
            # Attempt 1 is the trap: present, not null, but invalid.
            obj.Shape = (FakeShape(edges=4, volume=9.0) if attempts["n"] >= 2
                         else FakeShape(edges=4, volume=-500.0, valid=False))

        obj.on_recompute = on_recompute
        return obj

    doc.addObject = add
    op = {"op": "fillet", "name": "rounded", "target": "body", "radius": 8}
    obj = interpreter._edge_feature(op, doc, {"body": target}, "Part::Fillet", "radius")
    assert obj.Edges[0] == (1, 4.0, 4.0)  # 8 rejected, 4 accepted
    assert "used 4" in interpreter._drain_notes()[0]


# --------------------------------------------------------------------------- #
# optional fields sent as null (strict structured outputs)
# --------------------------------------------------------------------------- #
def test_an_explicit_null_optional_means_not_given():
    """Strict mode has no optional properties, so models send "axis": null."""
    assert interpreter._opt({}, "axis", [0, 0, -1]) == [0, 0, -1]
    assert interpreter._opt({"axis": None}, "axis", [0, 0, -1]) == [0, 0, -1]
    assert interpreter._opt({"axis": [1, 0, 0]}, "axis", [0, 0, -1]) == [1, 0, 0]


def test_a_deliberate_false_is_not_treated_as_missing():
    """The reason this is not just `op.get(k) or default`."""
    assert interpreter._opt({"fuse": False}, "fuse", True) is False
    assert interpreter._opt({"spacing2": 0}, "spacing2", 5) == 0
    assert interpreter._opt({"fuse": None}, "fuse", True) is True


def test_a_null_placement_member_is_the_same_as_an_absent_one():
    """`Vector(*None)` used to end the build the moment a strict reply arrived."""
    # The stubs record their constructor arguments, so comparing those compares
    # the placements: same position, same (identity) rotation.
    plain = interpreter._placement({"pos": [1, 2, 3]})
    nulled = interpreter._placement({"pos": [1, 2, 3], "rotation": None})
    assert plain.args[0].args == nulled.args[0].args == (1, 2, 3)
    assert plain.args[1].args == nulled.args[1].args == ()
    # An entirely null placement falls back to the origin instead of raising.
    assert interpreter._placement({"pos": None, "rotation": None}).args[0].args == (0, 0, 0)


# --------------------------------------------------------------------------- #
# holes that miss the material
# --------------------------------------------------------------------------- #
def test_a_hole_that_barely_grazes_the_part_is_caught():
    """Reported case: 0.283 mm3 removed where 141 mm3 was asked for, because
    the position sat on the bottom face with the hole drilling downwards."""
    op = {"op": "hole", "name": "hole", "target": "disk", "diameter": 6,
          "depth": 5, "position": [0, 0, 0]}
    try:
        interpreter._check_hole_bit(op, 0.283)
    except interpreter.InterpreterError as exc:
        assert "almost entirely outside 'disk'" in str(exc)
        assert "centre of the hole's TOP" in str(exc)
    else:
        raise AssertionError("a hole that removed almost nothing must fail")


def test_a_hole_that_does_its_job_passes():
    op = {"op": "hole", "name": "hole", "target": "disk", "diameter": 6,
          "depth": 5}
    interpreter._check_hole_bit(op, 141.0)          # the full expected volume
    interpreter._check_hole_bit(op, 45.0)           # a third of it: edge scallop


def test_a_through_hole_is_not_measured_against_its_stated_depth():
    """'depth' is ignored for a through hole - the part decides how deep."""
    op = {"op": "hole", "name": "h", "target": "d", "diameter": 6,
          "depth": 999, "through": True}
    interpreter._check_hole_bit(op, 141.0)


def test_only_holes_are_measured_this_way():
    interpreter._check_hole_bit({"op": "cut", "name": "c"}, 0.001)


# --------------------------------------------------------------------------- #
# revolve
# --------------------------------------------------------------------------- #
def test_revolve_defaults_to_a_full_revolution():
    doc = FakeDocument()
    op = {"op": "revolve", "name": "hub", "profile": [[0, 0], [5, 0], [5, 8]]}
    obj = interpreter._revolve(op, doc, {})
    assert obj.Shape.revolve_angle == 360
    assert "hub" in doc.objects


def test_revolve_honours_a_partial_angle():
    doc = FakeDocument()
    op = {"op": "revolve", "name": "half", "angle": 180,
          "profile": [[0, 0], [5, 0], [5, 8]]}
    obj = interpreter._revolve(op, doc, {})
    assert obj.Shape.revolve_angle == 180


def test_revolve_rejects_points_behind_the_axis():
    """A profile crossing the Z axis would self-intersect when revolved."""
    doc = FakeDocument()
    op = {"op": "revolve", "name": "bad",
          "profile": [[-2, 0], [5, 0], [5, 8]]}
    try:
        interpreter._revolve(op, doc, {})
    except interpreter.InterpreterError as exc:
        assert "r >= 0" in str(exc)
        assert "-2" in str(exc)
    else:
        raise AssertionError("expected InterpreterError for r < 0")


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
