"""Tests for the plain-English plan descriptions.

The panel shows these strings instead of asking the user to read JSON, so a
description that is merely *plausible* is a bug: it has to name the right
fields, in the right units, and it has to keep working when the plan it is
handed is broken - which is most of the time while someone is typing one.

Run with either::

    python tests/test_describe.py
    pytest tests/test_describe.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gpt4freecad.cad import describe, schema


# --------------------------------------------------------------------------- #
# Individual operations
# --------------------------------------------------------------------------- #
def test_a_box_reads_as_its_three_dimensions():
    text = describe.describe(
        {"op": "box", "name": "plate", "length": 40, "width": 40, "height": 10})
    assert text == "40 × 40 × 10 mm"


def test_dimensions_are_shown_in_the_plans_units():
    text = describe.describe(
        {"op": "box", "name": "plate", "length": 4, "width": 4, "height": 1},
        unit="in")
    assert text.endswith("in")
    assert "mm" not in text


def test_trailing_zeros_are_dropped():
    text = describe.describe(
        {"op": "box", "name": "b", "length": 40.0, "width": 2.50, "height": 10})
    assert "40.0" not in text and "2.50" not in text
    assert "40 × 2.5 × 10" in text


def test_radii_are_shown_as_diameters():
    # The IR stores a radius; a part is specified by its diameter, and reading
    # "Ø12" where the JSON says 6 is the whole point of the table.
    text = describe.describe(
        {"op": "cylinder", "name": "bore", "radius": 6, "height": 12})
    assert text == "Ø12 × 12 mm"


def test_a_hole_is_already_a_diameter_and_is_not_doubled():
    text = describe.describe(
        {"op": "hole", "name": "h", "target": "plate", "diameter": 5,
         "depth": 10, "position": [10, 10, 5]})
    assert text.startswith("Ø5 × 10 mm deep at (10, 10, 5)")
    assert "in plate" in text


def test_a_placement_is_reported():
    text = describe.describe(
        {"op": "cylinder", "name": "bore", "radius": 6, "height": 12,
         "placement": {"pos": [20, 20, -1]}})
    assert text.endswith("at (20, 20, -1)")


def test_a_full_sweep_is_not_worth_mentioning():
    plain = describe.describe(
        {"op": "cylinder", "name": "c", "radius": 5, "height": 5})
    full = describe.describe(
        {"op": "cylinder", "name": "c", "radius": 5, "height": 5, "angle": 360})
    assert plain == full == "Ø10 × 5 mm"


def test_a_partial_sweep_is():
    text = describe.describe(
        {"op": "cylinder", "name": "c", "radius": 5, "height": 5, "angle": 90})
    assert "90° slice" in text


def test_a_boolean_names_what_it_is_made_of():
    assert describe.describe(
        {"op": "cut", "name": "r", "base": "plate", "tool": "bore"}
    ) == "plate minus bore"
    assert describe.describe(
        {"op": "fuse", "name": "r", "parts": ["a", "b", "c"]}) == "a + b + c"


def test_cardinal_directions_are_named():
    text = describe.describe(
        {"op": "linear_pattern", "name": "row", "source": "tooth",
         "direction": [1, 0, 0], "count": 5, "spacing": 10})
    assert "along +X" in text
    assert "5 × tooth" in text and "10 mm apart" in text


def test_a_diagonal_direction_keeps_its_numbers():
    # Naming it would mean inventing a name; the numbers are the honest answer.
    text = describe.describe(
        {"op": "linear_pattern", "name": "row", "source": "tooth",
         "direction": [1, 1, 0], "count": 3, "spacing": 5})
    assert "(1, 1, 0)" in text


def test_a_profile_is_summarised_by_its_point_count():
    text = describe.describe(
        {"op": "extrude", "name": "p", "height": 10,
         "profile": [[0, 0], [10, 0], [10, 10], [0, 10]]})
    assert text == "4-point outline, 10 mm tall"


def test_omitting_the_edge_list_means_all_of_them():
    assert "all edges" in describe.describe(
        {"op": "fillet", "name": "f", "target": "r", "radius": 2})
    assert "edges 1, 3" in describe.describe(
        {"op": "fillet", "name": "f", "target": "r", "radius": 2, "edges": [1, 3]})


def test_every_operation_in_the_schema_has_wording():
    """A new op must not silently fall through to the generic description."""
    missing = [op for op in schema.OPERATIONS if op not in describe._DESCRIBERS]
    assert not missing, f"no description for: {missing}"


def test_the_example_program_describes_end_to_end():
    rows = describe.plan_rows(schema.example_program()["operations"])
    assert [row.op for row in rows] == ["box", "cylinder", "cut", "fillet"]
    assert all(row.detail for row in rows)


# --------------------------------------------------------------------------- #
# Whole programs
# --------------------------------------------------------------------------- #
def test_the_end_product_is_marked_and_the_scaffolding_is_not():
    rows = describe.plan_rows(schema.example_program()["operations"])
    assert [row.name for row in rows if row.result] == ["finished"]


def test_rows_are_numbered_from_one():
    rows = describe.plan_rows(schema.example_program()["operations"])
    assert [row.index for row in rows] == [1, 2, 3, 4]


def test_an_in_place_operation_produces_no_new_object():
    rows = describe.plan_rows([
        {"op": "box", "name": "plate", "length": 10, "width": 10, "height": 1},
        {"op": "translate", "target": "plate", "vector": [5, 0, 0]},
    ])
    assert rows[1].name == ""
    assert rows[1].detail == "plate moved by (5, 0, 0) mm"


def test_both_program_shapes_are_accepted():
    operations = schema.example_program()["operations"]
    assert describe.operations_of({"operations": operations}) == operations
    assert describe.operations_of(operations) == operations


def test_anything_else_yields_no_operations_rather_than_raising():
    for junk in (None, 42, "box", {}, {"operations": "box"}, {"ops": []}):
        assert describe.operations_of(junk) == []


# --------------------------------------------------------------------------- #
# Tolerance
#
# These rows are drawn from the plan box on every keystroke, so half-typed and
# outright wrong programs are the normal case, not the edge case.
# --------------------------------------------------------------------------- #
def test_an_unknown_operation_still_lists_its_fields():
    text = describe.describe({"op": "sprocket", "name": "s", "teeth": 12})
    assert "teeth 12" in text


def test_a_missing_field_does_not_raise():
    text = describe.describe({"op": "box", "name": "plate", "length": 40})
    assert text  # says something about a partly-typed box, whatever it can


def test_a_wrongly_typed_field_does_not_raise():
    text = describe.describe(
        {"op": "cylinder", "name": "c", "radius": "six", "height": 12})
    assert text


def test_a_program_that_is_not_a_list_describes_as_empty():
    assert describe.plan_rows("nonsense") == []
    assert describe.plan_rows(None) == []


def test_a_non_dict_operation_does_not_break_the_table():
    rows = describe.plan_rows([{"op": "box", "name": "b", "length": 1,
                                "width": 1, "height": 1}, "junk", 7])
    assert len(rows) == 3
    assert rows[1].op == "?"


def test_every_row_carries_its_own_json_for_the_tooltip():
    rows = describe.plan_rows(schema.example_program()["operations"])
    assert all('"op"' in row.source for row in rows)


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
