"""Engineering step-by-step timeline widget.

Owns ``self.program`` (the ordered list of IR ops = single source of truth). Steps
are added by AI (``generate_step``) or manually, edited via a schema-driven
:class:`OpForm`, reordered/deleted, and deterministically rebuilt through
``interpreter.rebuild``. The hosting :class:`~gpt4freecad.ui.panel.GPTPanel`
provides the shared NL input, the async worker, logging and post-build handling.
"""

from __future__ import annotations

from functools import partial

from .qt import QtWidgets, exec_dialog
from . import op_form
from .op_form import OpForm
from ..cad import prompts, schema
from .. import engine


class EngineeringWidget(QtWidgets.QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host
        self.program = []        # list of op dicts (source of truth)
        self.objects = {}        # IR name -> FreeCAD object (last build)
        self.created_names = []  # FreeCAD Names from last build (for removal)
        self._form = None
        self._pending_desc = ""  # last AI-step request, for repair prompts
        self._last_error = None  # why the last _apply_program/_rebuild failed
        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        self.setMinimumSize(0, 0)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(1)

        self.steps = QtWidgets.QListWidget()
        self.steps.setMinimumHeight(28)
        self.steps.setMaximumHeight(58)
        self.steps.currentRowChanged.connect(self._on_select)
        root.addWidget(self.steps)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(1)
        self._btn(row, "AI", self.add_ai, "Generate the next step")
        self._btn(row, "+", self._manual_add, "Add a manual step")
        self._btn(row, "Up", lambda: self._move(-1), "Move step up")
        self._btn(row, "Down", lambda: self._move(1), "Move step down")
        self._btn(row, "Del", self._delete, "Delete step")
        root.addLayout(row)

        self._form_host = QtWidgets.QWidget()
        self._form_layout = QtWidgets.QVBoxLayout(self._form_host)
        self._form_layout.setContentsMargins(0, 0, 0, 0)
        self._form_layout.setSpacing(1)

        form_scroll = QtWidgets.QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        form_scroll.setMinimumSize(0, 0)
        form_scroll.setWidget(self._form_host)
        root.addWidget(form_scroll, 1)

        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(1)
        self._btn(row2, "Apply", self._apply, "Apply parameter changes and rebuild")
        self._btn(row2, "Rebuild", self._rebuild, "Replay the whole timeline")
        root.addLayout(row2)

    def _btn(self, layout, text, slot, tip=""):
        b = QtWidgets.QPushButton(text)
        b.setMaximumHeight(26)
        b.clicked.connect(slot)
        if tip:
            b.setToolTip(tip)
        layout.addWidget(b)
        return b

    # ------------------------------------------------------------------ #
    # Names helpers
    # ------------------------------------------------------------------ #
    def _names(self):
        return [op["name"] for op in self.program
                if schema.OPERATIONS[op["op"]]["defines"] and "name" in op]

    def _names_before(self, idx):
        return [op["name"] for op in self.program[:idx]
                if schema.OPERATIONS[op["op"]]["defines"] and "name" in op]

    # ------------------------------------------------------------------ #
    # AI step
    # ------------------------------------------------------------------ #
    def add_ai(self):
        desc = self.host.input.toPlainText().strip()
        if not desc:
            self.host._set_status("Describe the next step first.", error=True)
            return
        ctx = self.host._gen_context()
        if ctx["provider"].requires_key and not ctx["api_key"]:
            self.host._log_error(f"No API key for {ctx['provider'].label}. Open Settings (⚙).")
            return
        if not self.host._local_model_ready(ctx["provider"]):
            return
        self.host._log_user(desc)
        self.host._repair.reset(self.host.cfg.repair_rounds())
        self._pending_desc = desc
        fn = partial(
            engine.generate_step, ctx["provider"], ctx["api_key"], ctx["model"],
            list(self.program), desc,
            units=ctx["units"], engineering=True, print_profile=ctx["print_profile"],
            temperature=ctx["temperature"], max_tokens=ctx["max_tokens"],
            thinking_level=ctx["thinking_level"], part_layout=ctx["part_layout"],
        )
        self.host._set_status("Asking for the next step…")
        self.host.run_worker(fn, self._on_step_generated)

    def _on_step_generated(self, result):
        new_ops = list(result.program or [])
        if not new_ops:
            self.host._log_error("Model returned no new operations.")
            return
        prior = list(self.program)
        combined = prior + new_ops
        if self._apply_program(combined, select=len(combined) - 1):
            note = " (auto-repaired)" if getattr(result, "repaired", False) else ""
            self.host._log_system(f"Added {len(new_ops)} step(s){note}.")
            self.host.input.clear()
            return
        error = self._last_error or "unknown build error"
        if self.program is combined:
            # Rebuild failed after the list was committed. The document itself
            # was already restored by the aborted transaction; roll back the list.
            self.program = prior
            self._refresh(len(prior) - 1 if prior else None)
            self.host._log_system("Rolled back the failed step(s).")
        self._maybe_repair_step(new_ops, error)

    def _maybe_repair_step(self, failed_ops, error):
        """Send a failed AI step back to the model, within the repair budget."""
        repair = self.host._repair
        if repair.seen_failure(failed_ops):
            self.host._log_error(
                "The model returned the same failing step again - stopping "
                "auto-repair. Edit the step or rephrase.")
            self.host._set_status("Auto-repair stalled.", error=True)
            return
        if not repair.can_retry():
            self.host._set_status("Step failed. Edit the step or rephrase.", error=True)
            return
        repair.note_failure(failed_ops)
        repair.start_attempt()
        ctx = self.host._gen_context()
        desc = prompts.step_repair_prompt(self._pending_desc, failed_ops, error)
        fn = partial(
            engine.generate_step, ctx["provider"], ctx["api_key"], ctx["model"],
            list(self.program), desc,
            units=ctx["units"], engineering=True, print_profile=ctx["print_profile"],
            temperature=ctx["temperature"], max_tokens=ctx["max_tokens"],
            thinking_level=ctx["thinking_level"], part_layout=ctx["part_layout"],
        )
        self.host._set_status(
            f"Step failed - asking the model for a fix (round {repair.round_label})…")
        self.host._log_system("Sending the step error back to the model for a fix…")
        self.host.run_worker(fn, self._on_step_generated)

    # ------------------------------------------------------------------ #
    # Manual step
    # ------------------------------------------------------------------ #
    def _manual_add(self):
        ops = list(schema.OPERATIONS.keys())
        op_type, ok = QtWidgets.QInputDialog.getItem(
            self, "Add step", "Operation:", ops, 0, False)
        if not ok or not op_type:
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Add '{op_type}' step")
        lay = QtWidgets.QVBoxLayout(dlg)
        form = OpForm(op_type, defined_names=self._names())
        lay.addWidget(form)
        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if exec_dialog(dlg) != QtWidgets.QDialog.Accepted:
            return
        try:
            op = form.value()
        except schema.SchemaError as exc:
            self.host._log_error(str(exc))
            return
        self._apply_program(list(self.program) + [op], select=len(self.program))

    # ------------------------------------------------------------------ #
    # Selection / editing
    # ------------------------------------------------------------------ #
    def _on_select(self, row):
        self._clear_form()
        if row < 0 or row >= len(self.program):
            return
        op = self.program[row]
        self._form = OpForm(op["op"], values=op, defined_names=self._names_before(row))
        self._form_layout.addWidget(self._form)

    def _clear_form(self):
        if self._form is not None:
            self._form.setParent(None)
            self._form.deleteLater()
            self._form = None

    def _apply(self):
        row = self.steps.currentRow()
        if row < 0 or self._form is None:
            return
        try:
            op = self._form.value()
        except schema.SchemaError as exc:
            self.host._log_error(str(exc))
            return
        new = list(self.program)
        new[row] = op
        self._apply_program(new, select=row)

    def _move(self, delta):
        row = self.steps.currentRow()
        new_row = row + delta
        if row < 0 or not (0 <= new_row < len(self.program)):
            return
        new = list(self.program)
        new[row], new[new_row] = new[new_row], new[row]
        self._apply_program(new, select=new_row)

    def _delete(self):
        row = self.steps.currentRow()
        if row < 0:
            return
        new = [op for i, op in enumerate(self.program) if i != row]
        self._apply_program(new, select=min(row, len(new) - 1))

    # ------------------------------------------------------------------ #
    # Program mutation + rebuild
    # ------------------------------------------------------------------ #
    def load_program(self, ops):
        """Replace the whole timeline with ``ops`` (e.g. a template) and rebuild."""
        ops = list(ops)
        return self._apply_program(ops, select=len(ops) - 1 if ops else None)

    def _apply_program(self, new_program, select=None):
        """Validate the candidate program; on success commit + rebuild.

        Returns True only if the program validated AND rebuilt; on failure
        ``self._last_error`` says why.
        """
        self._last_error = None
        if new_program:
            try:
                schema.validate_program({"operations": new_program})
            except schema.SchemaError as exc:
                self._last_error = str(exc)
                self.host._log_error(f"Change rejected: {exc}")
                return False
        self.program = new_program
        self._refresh(select)
        return self._rebuild()

    def _refresh(self, select=None):
        self.steps.blockSignals(True)
        self.steps.clear()
        for i, op in enumerate(self.program):
            self.steps.addItem(f"{i + 1}. {op_form.summarize(op)}")
        self.steps.blockSignals(False)
        if select is not None and 0 <= select < len(self.program):
            self.steps.setCurrentRow(select)
        else:
            self._clear_form()

    def _rebuild(self):
        from ..cad import interpreter  # lazy: needs FreeCAD
        self._last_error = None
        if not self.program:
            self._remove_created()
            self.objects, self.created_names = {}, []
            self.host._post_build(None)
            self.host._set_status("Timeline empty.")
            return True
        try:
            result, objects, log = interpreter.rebuild(
                self.program, prior_names=self.created_names,
                group_separate=(self.host._current_part_layout() == "separate"))
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            self.host._log_error(f"Rebuild failed: {exc}")
            self.host._set_status("Rebuild failed - edit the step.", error=True)
            return False
        for line in log:
            if line.startswith("note:"):  # deterministic in-build corrections
                self.host._log_system(line)
        self.objects = objects
        self.created_names = [o.Name for o in objects.values()]
        assembly_note = " as separate assembly components" if "__assembly__" in objects else ""
        self.host._log_system(f"Rebuilt {len(self.program)} step(s){assembly_note}.")
        self.host._post_build(result)
        self.host._set_status("Built.")
        return True

    def _remove_created(self):
        try:
            import FreeCAD as App
            doc = App.ActiveDocument
            if doc is None:
                return
            for name in reversed(self.created_names):
                try:
                    if doc.getObject(name) is not None:
                        doc.removeObject(name)
                except Exception:
                    pass
            doc.recompute()
        except Exception:
            pass

    def clear(self):
        self._remove_created()
        self.program, self.objects, self.created_names = [], {}, []
        self._refresh()
