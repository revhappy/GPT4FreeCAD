"""Tests for the panel's build -> inspect -> repair -> review decision-making.

The panel is where the deterministic checks and the model round-trips meet, and
none of it was covered: the methods live on a QWidget, so testing them looked
like it needed one. It does not. Each method here reads and writes plain
attributes and calls small helpers, so binding the real functions to a stand-in
object exercises the actual code with none of the Qt and none of FreeCAD.

Importing the panel module needs a Qt binding, which FreeCAD ships. Run with::

    freecadcmd tests/test_panel_flow.py

Without a Qt binding the file reports itself skipped rather than failing, so it
stays harmless in a plain ``python tests/...`` sweep.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gpt4freecad import harness
from gpt4freecad.cad import inspect as ginspect


# --------------------------------------------------------------------------- #
# Stand-ins
# --------------------------------------------------------------------------- #
class FakeShape:
    """Only what inspect_object actually asks a shape for."""

    def __init__(self, volume=1000.0, bbox=(10.0, 10.0, 10.0), null=False,
                 valid=True, solids=1):
        self.Volume = volume
        self.BoundBox = type("BB", (), {"XLength": bbox[0], "YLength": bbox[1],
                                        "ZLength": bbox[2]})()
        self._null, self._valid = null, valid
        shell = type("Shell", (), {"isClosed": lambda self: True})()
        solid = type("Solid", (), {"Shells": [shell]})()
        self.Solids = [solid] * solids

    def isNull(self):
        return self._null

    def isValid(self):
        return self._valid


class FakeObject:
    def __init__(self, label, shape=None):
        self.Label = self.Name = label
        self.Shape = shape if shape is not None else FakeShape()


class FakeWidget:
    """A text box / button / tab bar, as far as the flow code is concerned."""

    def __init__(self):
        self.text = ""
        self.enabled = None
        self.index = None

    def setPlainText(self, text):
        self.text = text

    def toPlainText(self):
        return self.text

    def setEnabled(self, value):
        self.enabled = value

    def setCurrentIndex(self, index):
        self.index = index

    def clear(self):
        self.text = ""


class FakeConfig:
    def __init__(self, auto_run=True):
        self._auto_run = auto_run

    def auto_run(self):
        return self._auto_run

    def repair_rounds(self):
        return 3


class FakeResult:
    def __init__(self, program=None, code=None, raw="", repaired=False):
        self.program, self.code, self.raw, self.repaired = program, code, raw, repaired


class FakePanel:
    """The attributes the flow methods touch, and nothing else."""

    def __init__(self, mode="structured", layout="fused", budget=3, auto_run=True):
        self._mode, self._layout = mode, layout
        self._repair = harness.RepairSession(budget)
        self._original_request = ""
        self._review_of = None
        self._pending_user = "ask"
        self._worker = None
        self.history = []
        self.last_result = None
        self.cfg = FakeConfig(auto_run)
        self.preview = FakeWidget()
        self.input = FakeWidget()
        self.build_btn = FakeWidget()
        self.output_tabs = FakeWidget()
        self.logs, self.errors, self.status = [], [], []
        self.sent = []        # prompts handed back to the model
        self.builds = 0       # times a rebuild was kicked off
        self.traces = []      # reasoning shown in the Thinking tab

    # --- the panel's own helpers, recorded instead of performed ---
    def _current_mode(self):
        return self._mode

    def _current_part_layout(self):
        return self._layout

    def _log_system(self, text):
        self.logs.append(text)

    def _log_error(self, text):
        self.errors.append(text)

    def _set_status(self, text, error=False):
        self.status.append(text)

    def _start_generation(self, message, log_as_user=True):
        self.sent.append(message)

    def show_reasoning(self, result):
        self.traces.append(getattr(result, "reasoning", ""))

    def _build_from_preview(self, user_reviewed=False):
        self.builds += 1

    # --- convenience ---
    @property
    def last_sent(self):
        return self.sent[-1] if self.sent else ""

    @property
    def log_text(self):
        return " | ".join(self.logs)


def _install_real_methods():
    """Borrow the real flow methods onto the stand-in class."""
    from gpt4freecad.ui.panel import GPTPanel

    for name in ("_inspect_program", "_try_review", "_try_geometry_repair",
                 "_on_generated", "_on_failed", "_try_plan_repair"):
        setattr(FakePanel, name, getattr(GPTPanel, name))


PROGRAM = [{"op": "box", "name": "plate", "length": 100, "width": 40, "height": 6}]
OTHER = [{"op": "box", "name": "plate", "length": 250, "width": 40, "height": 6}]
LEAVES = [{"ir_name": "plate", "name": "plate", "null": False, "valid": True,
           "solids": 1, "closed": True, "volume": 24000.0,
           "bbox": [100.0, 40.0, 6.0]}]


# --------------------------------------------------------------------------- #
# inspecting a whole program
# --------------------------------------------------------------------------- #
def test_inspect_program_measures_every_end_product():
    panel = FakePanel()
    ops = [
        {"op": "box", "name": "plate", "length": 40, "width": 40, "height": 10},
        {"op": "box", "name": "rib", "length": 5, "width": 5, "height": 5},
        {"op": "fillet", "name": "done", "target": "plate", "radius": 2},
    ]
    objects = {"plate": FakeObject("plate"), "rib": FakeObject("rib"),
               "done": FakeObject("done")}
    leaves, problems = panel._inspect_program(ops, objects)
    # 'plate' was consumed by the fillet; the rib and the result are left.
    assert [f["ir_name"] for f in leaves] == ["rib", "done"]
    assert any("2 separate solids" in p for p in problems)
    assert len(panel.logs) == 2  # one inspection line per end product


def test_inspect_program_accepts_several_solids_in_a_separate_layout():
    panel = FakePanel(layout="separate")
    ops = [
        {"op": "box", "name": "lid", "length": 5, "width": 5, "height": 5},
        {"op": "box", "name": "base", "length": 5, "width": 5, "height": 5},
    ]
    objects = {"lid": FakeObject("lid"), "base": FakeObject("base")}
    _leaves, problems = panel._inspect_program(ops, objects)
    assert problems == []


def test_inspect_program_survives_a_broken_program():
    panel = FakePanel()
    assert panel._inspect_program(None, None) == ([], [])


# --------------------------------------------------------------------------- #
# the review round: when it fires
# --------------------------------------------------------------------------- #
def test_review_stays_quiet_when_the_part_matches_the_request():
    panel = FakePanel()
    panel._original_request = "a 100mm long bracket"
    assert panel._try_review(PROGRAM, LEAVES) is False
    assert panel.sent == []
    assert panel._repair.attempts == 0  # cost nothing


def test_review_fires_when_a_measurement_disagrees():
    panel = FakePanel()
    panel._original_request = "a 250mm long bracket"
    assert panel._try_review(PROGRAM, LEAVES) is True
    assert panel._repair.attempts == 1
    assert panel._review_of == harness.fingerprint(PROGRAM)
    sent = panel.last_sent
    assert "a 250mm long bracket" in sent          # the request being checked
    assert "100.0 x 40.0 x 6.0 mm" in sent         # what was actually built
    assert "SAME program unchanged" in sent        # permission to sign it off


def test_review_needs_the_users_own_words():
    """Every round after the first sends a repair prompt, not the request."""
    panel = FakePanel()
    panel._original_request = ""
    assert panel._try_review(PROGRAM, LEAVES) is False


def test_review_respects_the_repair_budget():
    panel = FakePanel(budget=0)
    panel._original_request = "a 250mm long bracket"
    assert panel._try_review(PROGRAM, LEAVES) is False
    assert panel.sent == []


def test_review_is_structured_mode_only():
    panel = FakePanel(mode="python")
    panel._original_request = "a 250mm long bracket"
    assert panel._try_review(PROGRAM, LEAVES) is False


# --------------------------------------------------------------------------- #
# the review round: what comes back
# --------------------------------------------------------------------------- #
def test_an_unchanged_reply_signs_the_build_off():
    panel = FakePanel()
    panel._review_of = harness.fingerprint(PROGRAM)
    panel._on_generated(FakeResult(program=PROGRAM, raw="{}"))
    assert "confirmed" in panel.log_text
    assert panel.status[-1] == "Done - reviewed."
    assert panel.builds == 0        # the geometry is already there
    assert panel._review_of is None  # and the review is spent


def test_a_revised_reply_is_built():
    panel = FakePanel()
    panel._review_of = harness.fingerprint(PROGRAM)
    panel._on_generated(FakeResult(program=OTHER, raw="{}"))
    assert "revised plan" in panel.log_text
    assert panel.builds == 1
    assert panel._review_of is None


def test_a_revised_reply_waits_for_the_user_when_auto_run_is_off():
    panel = FakePanel(auto_run=False)
    panel._review_of = harness.fingerprint(PROGRAM)
    panel._on_generated(FakeResult(program=OTHER, raw="{}"))
    assert panel.builds == 0
    assert "Review the plan" in panel.status[-1]


def test_an_ordinary_generation_is_untouched_by_the_review_branch():
    panel = FakePanel()
    panel._on_generated(FakeResult(program=PROGRAM, raw="{}"))
    assert "confirmed" not in panel.log_text
    assert panel.builds == 1


def test_a_failed_request_drops_a_pending_review():
    """Otherwise a later plan could be mistaken for the reviewed one."""
    panel = FakePanel()
    panel._review_of = harness.fingerprint(PROGRAM)
    panel._on_failed("network is down")
    assert panel._review_of is None


# --------------------------------------------------------------------------- #
# geometry repair
# --------------------------------------------------------------------------- #
def test_geometry_repair_shows_the_model_what_was_built():
    panel = FakePanel()
    problems = ["'plate' result has zero (or negative) volume"]
    assert panel._try_geometry_repair(problems, PROGRAM, LEAVES) is True
    sent = panel.last_sent
    assert "zero (or negative) volume" in sent
    assert "What was actually built" in sent
    assert "plate: volume 24000.0 mm3" in sent
    assert panel._repair.seen_failure(PROGRAM)  # so a repeat is caught


def test_geometry_repair_works_without_measurements():
    panel = FakePanel()
    assert panel._try_geometry_repair(["something is wrong"], PROGRAM) is True
    assert "something is wrong" in panel.last_sent


def test_geometry_repair_gives_up_inside_the_budget():
    panel = FakePanel(budget=1)
    assert panel._try_geometry_repair(["bad"], PROGRAM, LEAVES) is True
    assert panel._try_geometry_repair(["bad"], PROGRAM, LEAVES) is False


def test_the_loop_guard_still_stops_a_repeated_failing_plan():
    panel = FakePanel()
    panel._repair.start_attempt()
    panel._repair.note_failure(PROGRAM)
    panel._on_generated(FakeResult(program=PROGRAM, raw="{}"))
    assert "same failing plan" in " ".join(panel.errors)
    assert panel.builds == 0


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def _run_all():
    try:
        _install_real_methods()
    except ImportError as exc:
        print(f"  SKIP tests/test_panel_flow.py - no Qt binding ({exc})")
        return 0
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


# No "__main__" guard: freecadcmd execs a script under its own module name, and
# FreeCAD is the interpreter that has the Qt binding this file needs. Running on
# import is therefore the only way it runs at all. pytest collects by importing,
# so it is the one caller that must not trigger the runner.
if "pytest" not in sys.modules:
    _failures = _run_all()
    # freecadcmd's embedded interpreter tears down on SystemExit without
    # flushing a piped stdout, which loses the whole report.
    sys.stdout.flush()
    sys.exit(1 if _failures else 0)
