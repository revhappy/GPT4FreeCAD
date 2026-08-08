"""The dockable GPT4FreeCAD panel - the primary interface."""

from __future__ import annotations

import json
import os
from functools import partial

from .qt import QtCore, QtGui, QtWidgets, guard_wheel
from .worker import LLMWorker
from .settings import open_settings
from .engineering import EngineeringWidget
from .plan_table import PlanTable
from . import op_form, theme
from ..config import get_config
from ..llm import all_providers, get_provider
from .. import engine, harness
from ..cad import describe, prompts, schema, templates

_UNITS = ["mm", "cm", "m", "in"]
_MODES = [("Structured", "structured"),
          ("Engineering", "engineering"),
          ("Python", "python")]
_MODE_INDEX = {m[1]: i for i, m in enumerate(_MODES)}
# Gemini 3 reasoning depth. "Default" = let the model decide (don't send the param).
_THINKING = [("Default", "default"), ("Minimal", "minimal"), ("Low", "low"),
             ("Medium", "medium"), ("High", "high")]
_UI_VERSION = "compact-7"
# Shown in the model dropdown when the local provider has no .gguf chosen yet.
_NO_LOCAL_MODEL = "(choose a model…)"
# Keep this many recent conversation messages (user + assistant); older turns
# are dropped so a long session cannot grow the prompt without bound.
_HISTORY_MAX = 24


class GPTPanel(QtWidgets.QWidget):
    # Class defaults, because Qt delivers changeEvent during _build_ui - the
    # first setStyleSheet is itself a style change - and again from inside
    # ensurePolished. Without these, recolouring runs before the widgets it
    # recolours exist, and then recurses into itself.
    _themable = False   # the labels _apply_theme touches are not built yet
    _theming = False    # _apply_theme is already running
    _status_error = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cfg = get_config()
        self.history = []            # multi-turn conversation (user/assistant)
        self.last_result = None
        self._last_built = None      # last result object (for Export STL / scale)
        self._worker = None
        self._pending_user = ""
        self._original_request = ""   # the user's own words, not a repair prompt
        self._review_of = None        # fingerprint of a program under review
        self._repair = harness.RepairSession(self.cfg.repair_rounds())
        self._models_refreshed = set()   # provider ids re-scanned this session
        self._model_workers = {}         # keeps refresh threads alive in flight
        self._loading = True
        self._build_ui()
        self._load_state()
        self._loading = False

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        """Build a compact panel whose primary controls never leave the viewport."""
        self.setMinimumSize(180, 190)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
        self.setStyleSheet("""
            QLabel, QCheckBox { font-size: 10px; }
            QComboBox, QPushButton, QToolButton, QLineEdit,
            QSpinBox, QDoubleSpinBox {
                font-size: 10px;
                min-height: 18px;
                max-height: 20px;
                padding: 0px 2px;
            }
            QPlainTextEdit, QTextEdit, QListWidget { font-size: 10px; }
            QTabBar::tab { font-size: 10px; padding: 2px 7px; }
        """)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(2)

        def compact_combo(combo, visible_chars):
            combo.setSizeAdjustPolicy(
                QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(visible_chars)
            combo.setSizePolicy(
                QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)

        prompt_row = QtWidgets.QHBoxLayout()
        prompt_row.setSpacing(2)
        self.input = QtWidgets.QPlainTextEdit()
        self.input.setPlaceholderText("Describe the part or next step...")
        self.input.setFixedHeight(60)
        prompt_row.addWidget(self.input, 1)

        self.generate_btn = QtWidgets.QPushButton("Generate")
        self.generate_btn.setDefault(True)
        self.generate_btn.setFixedSize(72, 60)
        self.generate_btn.setToolTip("Generate a CAD plan from the description.")
        self.generate_btn.clicked.connect(self._on_generate)
        prompt_row.addWidget(self.generate_btn)
        root.addLayout(prompt_row)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(2)
        self.provider_combo = QtWidgets.QComboBox()
        compact_combo(self.provider_combo, 7)
        self.provider_combo.setToolTip("AI provider")
        for provider in all_providers():
            self.provider_combo.addItem(provider.label, provider.id)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        header.addWidget(self.provider_combo, 2)

        self.model_combo = QtWidgets.QComboBox()
        compact_combo(self.model_combo, 8)
        self.model_combo.setToolTip("Model")
        self.model_combo.setEditable(True)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        # Commit signals, not every keystroke: a model is "picked" when it is
        # chosen from the list or an edit is finished, and only then is it
        # worth keeping on the dropdown for next time.
        self.model_combo.activated.connect(self._remember_current_model)
        self._wire_model_commit()
        header.addWidget(self.model_combo, 3)

        self.settings_btn = QtWidgets.QToolButton()
        self.settings_btn.setText("...")
        self.settings_btn.setFixedWidth(28)
        self.settings_btn.setToolTip("Settings: API keys, models, and 3D printing")
        self.settings_btn.clicked.connect(self._open_settings)
        header.addWidget(self.settings_btn)
        root.addLayout(header)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.setSpacing(2)
        self.mode_combo = QtWidgets.QComboBox()
        compact_combo(self.mode_combo, 7)
        self.mode_combo.setToolTip("Generation mode")
        for label, _value in _MODES:
            self.mode_combo.addItem(label)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_combo, 3)

        self.part_layout_combo = QtWidgets.QComboBox()
        compact_combo(self.part_layout_combo, 6)
        self.part_layout_combo.addItem("Fused", "fused")
        self.part_layout_combo.addItem("Separate", "separate")
        self.part_layout_combo.setToolTip(
            "Fused: one finished solid. Separate: independently editable assembly components.")
        self.part_layout_combo.currentIndexChanged.connect(
            self._on_part_layout_changed)
        mode_row.addWidget(self.part_layout_combo, 2)

        self.units_label = QtWidgets.QLabel("U")
        self.units_label.setToolTip("Units")
        mode_row.addWidget(self.units_label)
        self.units_combo = QtWidgets.QComboBox()
        compact_combo(self.units_combo, 2)
        self.units_combo.setToolTip("Units")
        self.units_combo.addItems(_UNITS)
        self.units_combo.currentTextChanged.connect(self._on_units_changed)
        mode_row.addWidget(self.units_combo, 1)

        self.thinking_label = QtWidgets.QLabel("T")
        self.thinking_label.setToolTip("Thinking level")
        mode_row.addWidget(self.thinking_label)
        self.thinking_combo = QtWidgets.QComboBox()
        compact_combo(self.thinking_combo, 4)
        for label, value in _THINKING:
            self.thinking_combo.addItem(label, value)
        self.thinking_combo.setToolTip("Gemini reasoning depth")
        self.thinking_combo.currentIndexChanged.connect(self._on_thinking_changed)
        mode_row.addWidget(self.thinking_combo, 1)

        self.print_check = QtWidgets.QCheckBox("3D")
        self.print_check.setToolTip(
            "3D-print mode: printability, bed-fit checks, and STL export")
        self.print_check.toggled.connect(self._on_print_toggled)
        mode_row.addWidget(self.print_check)
        root.addLayout(mode_row)

        self.template_row = QtWidgets.QWidget()
        template_layout = QtWidgets.QHBoxLayout(self.template_row)
        template_layout.setContentsMargins(0, 0, 0, 0)
        template_layout.setSpacing(2)
        self.template_combo = QtWidgets.QComboBox()
        compact_combo(self.template_combo, 10)
        self.template_combo.setToolTip(
            "Insert a ready-made parametric starter program - no API call. "
            "Tweak its parameters or ask the AI to modify it.")
        self.template_combo.activated.connect(self._on_template_activated)
        template_layout.addWidget(self.template_combo, 1)
        self.template_save_btn = QtWidgets.QToolButton()
        self.template_save_btn.setText("Save")
        self.template_save_btn.setToolTip("Save the current plan as a template")
        self.template_save_btn.clicked.connect(self._on_save_template)
        template_layout.addWidget(self.template_save_btn)
        root.addWidget(self.template_row)

        self.output_tabs = QtWidgets.QTabWidget()
        self.output_tabs.setMinimumHeight(48)
        self.output_tabs.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)

        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumSize(0, 0)
        self.output_tabs.addTab(self.log, "Activity")

        self.stack = QtWidgets.QStackedWidget()
        self.stack.setMinimumSize(0, 0)

        casual = QtWidgets.QWidget()
        casual_layout = QtWidgets.QVBoxLayout(casual)
        casual_layout.setContentsMargins(0, 0, 0, 0)
        casual_layout.setSpacing(1)
        self.preview_label = QtWidgets.QLabel("Plan")
        self.preview_label.setVisible(False)
        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setPlaceholderText("Generated plan or Python appears here.")
        mono = QtGui.QFont("Consolas")
        mono.setFixedPitch(True)
        self.preview.setFont(mono)
        self.preview.setMinimumSize(0, 0)

        # Both panes edit the plan; the JSON stays the single source of truth,
        # and the table reads from and writes back to that same text rather
        # than holding a second copy of the program. Editing through the table
        # is the path that matters - someone who wants a 60mm plate to be 80mm
        # should not have to be comfortable in JSON to say so.
        self.plan_table = PlanTable()
        self.plan_table.setToolTip(
            "The plan, step by step. Double-click a step to change it, "
            "Delete to remove it.")
        self.plan_table.editRequested.connect(self._edit_plan_step)
        self.plan_table.removeRequested.connect(self._remove_plan_step)
        self.preview.textChanged.connect(self._refresh_plan_table)
        self.plan_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.plan_split.setChildrenCollapsible(True)
        self.plan_split.setHandleWidth(3)
        self.plan_split.addWidget(self.plan_table)
        self.plan_split.addWidget(self.preview)
        self.plan_split.setStretchFactor(0, 3)
        self.plan_split.setStretchFactor(1, 2)
        self.plan_split.setMinimumSize(0, 0)
        casual_layout.addWidget(self.plan_split, 1)
        self.stack.addWidget(casual)

        self.eng = EngineeringWidget(self)
        self.stack.addWidget(self.eng)
        self.output_tabs.addTab(self.stack, "Plan")
        self.output_tabs.addTab(self._build_thinking_tab(mono), "Thinking")
        self.output_tabs.addTab(self._build_prompt_tab(mono), "Prompt")
        root.addWidget(self.output_tabs, 1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(2)
        self.build_btn = QtWidgets.QPushButton("Build")
        self.build_btn.setToolTip("Build geometry from the plan.")
        self.build_btn.clicked.connect(self._on_build_clicked)
        self.build_btn.setEnabled(False)
        buttons.addWidget(self.build_btn, 1)

        self.export_btn = QtWidgets.QPushButton("STL")
        self.export_btn.setToolTip("Export the built result as STL (mesh, for 3D printing).")
        self.export_btn.clicked.connect(self._on_export_stl)
        self.export_btn.setEnabled(False)
        buttons.addWidget(self.export_btn, 1)

        self.step_btn = QtWidgets.QPushButton("STEP")
        self.step_btn.setToolTip("Export the built result as STEP (exact geometry, for CAD/CAM).")
        self.step_btn.clicked.connect(self._on_export_step)
        self.step_btn.setEnabled(False)
        buttons.addWidget(self.step_btn, 1)

        self.undo_btn = QtWidgets.QPushButton("Undo")
        self.undo_btn.clicked.connect(self._on_undo)
        buttons.addWidget(self.undo_btn)

        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.clear_btn.clicked.connect(self._on_clear)
        buttons.addWidget(self.clear_btn)
        root.addLayout(buttons)

        self.status = QtWidgets.QLabel("Ready.")
        self.status.setMaximumHeight(14)
        root.addWidget(self.status)

        self._themable = True
        self._apply_theme()
        # The panel is a tall, narrow dock full of dropdowns. Scrolling it must
        # scroll it, not spin whatever control happens to be under the pointer.
        guard_wheel(self)
        self._reload_templates()

    # ------------------------------------------------------------------ #
    # Theme
    # ------------------------------------------------------------------ #
    def _apply_theme(self):
        """Recolour the secondary labels for the theme that is loaded.

        See :mod:`gpt4freecad.ui.theme`: FreeCAD's stylesheets move the
        background far enough between themes that a fixed grey is legible in
        one and invisible in the other.
        """
        if not self._themable or self._theming:
            return
        self._theming = True
        try:
            muted = f"color: {theme.muted(self)};"
            for label in (self.usage_label, self.prompt_status):
                label.setStyleSheet(muted)
            self.status.setStyleSheet(
                f"color: {theme.danger(self)};" if self._status_error else muted)
        finally:
            self._theming = False

    def changeEvent(self, event):
        if event.type() in (QtCore.QEvent.PaletteChange,
                            QtCore.QEvent.StyleChange):
            self._apply_theme()
        super().changeEvent(event)

    def showEvent(self, event):
        # A widget only inherits the stylesheet's colours once it is polished
        # inside the window, which has not happened while _build_ui runs.
        self._apply_theme()
        super().showEvent(event)

    # ------------------------------------------------------------------ #
    # Thinking + prompt tabs
    # ------------------------------------------------------------------ #
    def _build_thinking_tab(self, mono):
        """The model's reasoning for the last reply, and what it cost.

        Read-only: this is a record of what happened, not an input. Providers
        that do not return reasoning say so here rather than showing a blank.
        """
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self.usage_label = QtWidgets.QLabel("")  # coloured by _apply_theme
        self.usage_label.setVisible(False)
        layout.addWidget(self.usage_label)

        self.thinking = QtWidgets.QPlainTextEdit()
        self.thinking.setReadOnly(True)
        self.thinking.setFont(mono)
        self.thinking.setMinimumSize(0, 0)
        self.thinking.setPlaceholderText(
            "The model's reasoning appears here after a generation.")
        layout.addWidget(self.thinking, 1)
        return page

    def _build_prompt_tab(self, mono):
        """The system prompt, editable.

        The box always shows what will actually be sent - your own text once you
        save one, the built-in instructions otherwise - so the prompt is never a
        black box even if you never edit it. Overrides are stored per mode.
        """
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self.prompt_status = QtWidgets.QLabel("")  # coloured by _apply_theme
        self.prompt_status.setWordWrap(True)
        layout.addWidget(self.prompt_status)

        self.prompt_edit = QtWidgets.QPlainTextEdit()
        self.prompt_edit.setFont(mono)
        self.prompt_edit.setMinimumSize(0, 0)
        self.prompt_edit.setToolTip(
            "The instructions sent with every request in this mode. Edit and "
            "Save to steer the model directly; Reset restores the built-in "
            "prompt. Engineering steps always get the program so far appended, "
            "whatever this says.")
        layout.addWidget(self.prompt_edit, 1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(2)
        save = QtWidgets.QToolButton()
        save.setText("Save prompt")
        save.setToolTip("Use this text for every request in the current mode.")
        save.clicked.connect(self._save_prompt)
        buttons.addWidget(save)
        reset = QtWidgets.QToolButton()
        reset.setText("Reset")
        reset.setToolTip("Discard your prompt and go back to the built-in one.")
        reset.clicked.connect(self._reset_prompt)
        buttons.addWidget(reset)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return page

    def _default_system_prompt(self, mode=None) -> str:
        """The built-in instructions for a mode, assembled as they would be sent."""
        mode = mode or self._current_mode()
        if mode == "python":
            return prompts.PYTHON_SYSTEM_PROMPT
        return prompts.system_prompt(
            self.units_combo.currentText(),
            engineering=(mode == "engineering"),
            print_profile=self.cfg.print_profile(),
            part_layout=self._current_part_layout(),
        )

    def _refresh_prompt_tab(self):
        """Show the prompt for the current mode - the user's or the built-in."""
        mode = self._current_mode()
        override = self.cfg.system_prompt(mode)
        self.prompt_edit.setPlainText(override or self._default_system_prompt(mode))
        self.prompt_status.setText(
            f"Your own prompt, used for {mode} mode." if override
            else f"Built-in {mode} prompt. Edit and Save to use your own.")

    def _save_prompt(self):
        mode = self._current_mode()
        text = self.prompt_edit.toPlainText().strip()
        # Saving the built-in text unchanged means "no override" - otherwise the
        # prompt would silently freeze at today's wording.
        if not text or text == self._default_system_prompt(mode).strip():
            self._reset_prompt()
            return
        self.cfg.set_system_prompt(mode, text)
        self._refresh_prompt_tab()
        self._log_system(f"Using your own system prompt for {mode} mode.")

    def _reset_prompt(self):
        mode = self._current_mode()
        had_override = bool(self.cfg.system_prompt(mode))
        self.cfg.set_system_prompt(mode, "")
        self._refresh_prompt_tab()
        if had_override:
            self._log_system(f"Restored the built-in {mode} prompt.")

    def show_reasoning(self, result):
        """Fill the Thinking tab from a generation result. Safe to call always."""
        usage = getattr(result, "usage", None) or {}
        reasoning = (getattr(result, "reasoning", "") or "").strip()

        if usage:
            parts = [f"{name} {count:,}" for name, count in (
                ("in", usage.get("input", 0)), ("out", usage.get("output", 0)),
                ("thinking", usage.get("reasoning", 0)),
                ("cached", usage.get("cached", 0))) if count]
            self.usage_label.setText("tokens: " + ", ".join(parts))
        self.usage_label.setVisible(bool(usage))

        if reasoning:
            self.thinking.setPlainText(reasoning)
        elif usage.get("reasoning"):
            # OpenAI's Chat Completions endpoint bills reasoning tokens but
            # never returns the text. Saying so beats an empty box.
            self.thinking.setPlainText(
                f"This model reasoned for {usage['reasoning']:,} tokens, but "
                "this provider's API does not return the reasoning text.")
        else:
            self.thinking.setPlainText(
                "This model did not report any reasoning for the last reply.")

    # ------------------------------------------------------------------ #
    # State load / persistence
    # ------------------------------------------------------------------ #
    def _load_state(self):
        self._drop_catalogue_cache()
        pid = self.cfg.provider()
        idx = self.provider_combo.findData(pid)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        self._populate_models()

        mode = self.cfg.mode()
        self.mode_combo.setCurrentIndex(_MODE_INDEX.get(mode, 0))
        self._apply_mode_visibility(mode)

        layout_index = self.part_layout_combo.findData(self.cfg.part_layout())
        if layout_index >= 0:
            self.part_layout_combo.setCurrentIndex(layout_index)

        ui = self.units_combo.findText(self.cfg.units())
        if ui >= 0:
            self.units_combo.setCurrentIndex(ui)

        ti = self.thinking_combo.findData(self.cfg.thinking_level())
        if ti >= 0:
            self.thinking_combo.setCurrentIndex(ti)
        self._apply_provider_visibility(pid)

        self.print_check.setChecked(self.cfg.print_mode())
        self._update_print_tooltip()
        # Units, layout and print mode all feed the built-in prompt, so refresh
        # it once they are loaded rather than only on the next mode change.
        self._refresh_prompt_tab()

        if not self._any_key_set():
            self._log_system(
                "Welcome to GPT4FreeCAD. Open Settings (⚙) to add an API key for Gemini, "
                "OpenAI, or Claude - or pick the 'Local (Machine Activation)' provider to "
                "run a model on this machine with no key and no cloud. Then describe a "
                "part below.")

    def _drop_catalogue_cache(self):
        """Discard a cloud catalogue cached by an earlier version.

        2.10.0 briefly cached each provider's whole catalogue under the same
        key the local scan uses - hundreds of entries per provider, sitting in
        the config file for a dropdown that no longer reads them. Nothing needs
        that data now, so clear it rather than leave it behind.
        """
        for provider in all_providers():
            if provider.id != "machine" and self.cfg.discovered_models(provider.id):
                self.cfg.set_discovered_models(provider.id, [])

    def _populate_models(self):
        """Fill the dropdown with every model we know this provider has.

        Choosing a model is the single most common thing anyone does here, and
        it used to mean opening Settings and hunting: the panel listed only a
        provider's hardcoded defaults, and exactly one entry - the file chosen
        in Settings - for the local provider.

        What goes in depends on what "all your models" honestly means for the
        provider:

        * local - every .gguf found on this machine. That is a handful of
          files, and they are genuinely all yours.
        * cloud - the built-in defaults plus every model you have picked
          before. *Not* the provider's catalogue: OpenRouter alone lists 400
          models, which is a worse dropdown than the one this replaced. Picking
          a model adds it here permanently, so it is still on the list after
          you switch away and back.

        The local list is cached and refreshed behind the dropdown (see
        :meth:`_refresh_models`), because scanning the disk cannot happen while
        the panel is being built.
        """
        provider = self._current_provider()
        known = self.cfg.discovered_models(provider.id)
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        # A cloud model id can be typed; a local one is a file on this machine,
        # and typing its description would not name anything.
        self.model_combo.setEditable(provider.id != "machine")
        self._wire_model_commit()  # setEditable rebuilds the line edit

        if provider.id == "machine":
            # The "model" is a .gguf on this machine, so the entry carries the
            # path as its data and a readable description as its label - a bare
            # filename says nothing about size or where it came from.
            path = self.cfg.machine_model_path()
            for label, value in known:
                self.model_combo.addItem(label, value)
            if path and self.model_combo.findData(path) < 0:
                self.model_combo.insertItem(0, os.path.basename(path), path)
            if self.model_combo.count() == 0:
                self.model_combo.addItem(_NO_LOCAL_MODEL, "")
            index = self.model_combo.findData(path) if path else -1
            self.model_combo.setCurrentIndex(max(index, 0))
            self.model_combo.setToolTip(
                path or "No local model yet — models found on this computer "
                        "appear here.")
        else:
            saved = self.cfg.model(provider.id, provider.default_model)
            # The model in use counts as picked, even if it was set before this
            # list existed or chosen in Settings. Without this it would be on
            # the dropdown only while it stayed selected, and would vanish the
            # moment you switched to another one - which is the whole problem.
            # Guarded so a model already on the list is not reordered on every
            # repopulate.
            remembered = self.cfg.remembered_models(provider.id)
            if (saved and saved not in provider.default_models
                    and saved not in [value for _label, value in remembered]):
                self.cfg.remember_model(provider.id, saved)
                remembered = self.cfg.remembered_models(provider.id)

            # Defaults first - they are the curated, known-good ones - then
            # everything this user has picked before, minus duplicates.
            items = list(provider.default_models)
            for _label, value in remembered:
                if value not in items:
                    items.append(value)
            # A provider whose models all come from a live catalogue (Ollama /
            # LM Studio) ships no defaults, so without this the dropdown would
            # be empty even though a model is configured.
            if saved and saved not in items:
                items.insert(0, saved)
            for value in items:
                self.model_combo.addItem(value, value)
            self.model_combo.setCurrentText(saved)
            self.model_combo.setToolTip(
                "Model — pick a new one in Settings (…) and it stays on this list")

        self.model_combo.blockSignals(False)
        self._refresh_models()

    def _refresh_models(self):
        """Re-scan this machine for local models, in the background, once.

        Local only. A cloud provider's catalogue is deliberately *not* fetched
        here: it is hundreds of entries, and a dropdown of hundreds is worse
        than the short one it would replace. Cloud models reach the dropdown by
        being picked - see :meth:`_remember_current_model`.

        Deliberately quiet. It runs on its own thread so the panel never
        blocks, it leaves the current selection alone, and a failure is not
        reported: the dropdown already holds the cached list, so there is
        nothing for the user to do about it and nothing to interrupt them for.

        Once per session, because the disk does not change underneath us often
        enough to be worth re-walking on every provider switch.
        """
        provider = self._current_provider()
        if provider.id != "machine" or provider.id in self._models_refreshed:
            return

        from ..llm import discovery

        self._models_refreshed.add(provider.id)
        worker = LLMWorker(discovery.local_models, self)
        worker.succeeded.connect(partial(self._on_models_found, provider.id))
        # Silent by design - see the docstring.
        worker.failed.connect(lambda _message: None)
        # Held on the panel so the thread is not collected mid-flight.
        self._model_workers[provider.id] = worker
        worker.start()

    def _on_models_found(self, provider_id, found):
        """Cache the local scan's result and redraw the dropdown."""
        from ..llm import discovery

        entries = [[discovery.describe(m), m["path"]] for m in (found or [])
                   if m.get("path")]
        if not entries:
            return

        previous = self.cfg.discovered_models(provider_id)
        self.cfg.set_discovered_models(provider_id, entries)
        # Only redraw if this is still the provider on screen, and only if the
        # list actually changed - repopulating fights a user mid-selection.
        if previous != entries and self.provider_combo.currentData() == provider_id:
            selected = self.model_combo.currentText()
            self._populate_models()
            if selected and self.model_combo.findText(selected) >= 0:
                self.model_combo.setCurrentText(selected)

    def _wire_model_commit(self):
        """Connect the editor's commit signal, once per editor.

        Toggling the combo between editable and not destroys and rebuilds its
        line edit, so this has to run again after every such switch - and must
        not connect twice to an editor it has already wired, or a single commit
        would be recorded twice.
        """
        editor = self.model_combo.lineEdit()
        if editor is not None and not editor.property("gpt4freecad_wired"):
            editor.setProperty("gpt4freecad_wired", True)
            editor.editingFinished.connect(self._remember_current_model)

    def _remember_current_model(self, *_args):
        """Keep a model the user has settled on, so it stays in the dropdown.

        Wired to *commit* signals only - picking from the list, or finishing an
        edit - never to ``currentTextChanged``, which fires on every keystroke
        and would remember "g", "gp", "gpt-" on the way to a model name.
        """
        provider_id = self.provider_combo.currentData()
        if self._loading or provider_id == "machine":
            return  # a local model is a file, already handled by the scan
        model = self.model_combo.currentText().strip()
        if model:
            self.cfg.remember_model(provider_id, model)

    def _any_key_set(self) -> bool:
        return any(self.cfg.api_key(p.id) for p in all_providers())

    # ------------------------------------------------------------------ #
    # Signal handlers (settings)
    # ------------------------------------------------------------------ #
    def _current_provider(self):
        return get_provider(self.provider_combo.currentData())

    def _current_mode(self) -> str:
        return _MODES[self.mode_combo.currentIndex()][1]

    def _current_part_layout(self) -> str:
        return self.part_layout_combo.currentData() or "fused"

    def _on_provider_changed(self, _index):
        if self._loading:
            return
        pid = self.provider_combo.currentData()
        self.cfg.set_provider(pid)
        self._populate_models()
        self._apply_provider_visibility(pid)

    def _apply_provider_visibility(self, provider_id):
        is_gemini = provider_id == "gemini"
        self.thinking_label.setVisible(is_gemini)
        self.thinking_combo.setVisible(is_gemini)

    def _on_thinking_changed(self, _index):
        if not self._loading:
            self.cfg.set_thinking_level(self.thinking_combo.currentData())

    def _on_model_changed(self, text):
        if self._loading:
            return
        provider_id = self.provider_combo.currentData()
        if provider_id == "machine":
            # The local "model" is a file, and the entry's label describes it
            # rather than naming it - so the choice is the item's data, and it
            # is saved straight from here instead of only from Settings.
            path = self.model_combo.currentData()
            if path:
                self.cfg.set_machine_model_path(path)
                self.model_combo.setToolTip(path)
            return
        self.cfg.set_model(provider_id, text.strip())

    def _on_mode_changed(self, _index):
        mode = self._current_mode()
        if not self._loading:
            self.cfg.set_mode(mode)
        self._apply_mode_visibility(mode)

    def _on_part_layout_changed(self, _index):
        if self._loading:
            return
        layout = self._current_part_layout()
        self.cfg.set_part_layout(layout)
        self._refresh_prompt_tab()  # the layout choice is spelled out in the prompt
        if layout == "separate":
            self._set_status("New parts will stay separate and editable.")
        else:
            self._set_status("New features will use the fused single-part workflow.")

    def _apply_mode_visibility(self, mode):
        is_struct = mode == "structured"
        is_eng = mode == "engineering"
        self.stack.setCurrentIndex(1 if is_eng else 0)
        # Python mode has no operations to tabulate, and the engineering
        # timeline is its own view of the same thing.
        self.plan_table.setVisible(is_struct)
        self.units_label.setVisible(is_struct or is_eng)
        self.units_combo.setVisible(is_struct or is_eng)
        self.print_check.setVisible(is_struct or is_eng)
        self.part_layout_combo.setVisible(is_struct or is_eng)
        self.build_btn.setVisible(not is_eng)
        self.template_row.setVisible(is_struct or is_eng)
        self.preview_label.setText("Plan (JSON):" if is_struct else "Python code:")
        self.output_tabs.setTabText(1, "Steps" if is_eng else "Plan")
        self.generate_btn.setText(self._generate_idle_label())
        # Each mode has its own prompt; the tab must follow the mode.
        self._refresh_prompt_tab()
        self._refresh_plan_table()

    def _on_units_changed(self, text):
        if self._loading:
            return
        self.cfg.set_units(text)
        self._refresh_prompt_tab()  # units are quoted in the built-in prompt
        self._refresh_plan_table()  # every dimension in the table is in them

    def _on_print_toggled(self, checked):
        if not self._loading:
            self.cfg.set_print_mode(checked)
        self._update_print_tooltip()
        if not self._loading:
            self._refresh_prompt_tab()  # print mode adds a whole addendum

    def _update_print_tooltip(self):
        self.print_check.setToolTip(
            f"Design for FDM 3D printing; build volume "
            f"{self.cfg.bed_x():.0f}×{self.cfg.bed_y():.0f}×{self.cfg.bed_z():.0f} mm "
            f"(set in ⚙ Settings).")

    def _open_settings(self):
        if open_settings(self):
            self._loading = True
            self._load_state()
            self._loading = False
            self._log_system("Settings saved.")

    # ------------------------------------------------------------------ #
    # Template library
    # ------------------------------------------------------------------ #
    def _reload_templates(self):
        combo = self.template_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Template…", None)
        for template in templates.builtin_templates():
            combo.addItem(template["name"], template)
            combo.setItemData(combo.count() - 1, template["description"],
                              QtCore.Qt.ToolTipRole)
        user, problems = templates.user_templates()
        if user:
            combo.insertSeparator(combo.count())
            for template in user:
                combo.addItem(template["name"], template)
                combo.setItemData(combo.count() - 1, template["description"],
                                  QtCore.Qt.ToolTipRole)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)
        for problem in problems:
            self._log_error(f"Template skipped: {problem}")

    def _on_template_activated(self, index):
        template = self.template_combo.itemData(index)
        self.template_combo.setCurrentIndex(0)
        if not template:
            return
        ops = templates.template_program(template)["operations"]
        label = template["name"]
        if self._current_mode() == "engineering":
            if self.eng.program:
                ret = QtWidgets.QMessageBox.question(
                    self, "Replace timeline",
                    f"Replace the current {len(self.eng.program)}-step timeline "
                    f"with the '{label}' template?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
                if ret != QtWidgets.QMessageBox.Yes:
                    return
            if self.eng.load_program(ops):
                self._log_system(
                    f"Loaded template '{label}' ({len(ops)} steps). Select a step "
                    "to tweak its parameters, or describe the next feature.")
        else:
            program_json = json.dumps({"operations": ops}, indent=2)
            self.preview.setPlainText(program_json)
            # Seed the conversation so follow-up requests refine the template.
            self.history.append(
                {"role": "user", "content": f"Start from this '{label}' starter part."})
            self.history.append({"role": "assistant", "content": program_json})
            self.build_btn.setEnabled(True)
            self.output_tabs.setCurrentIndex(1)
            self._log_system(
                f"Loaded template '{label}' ({len(ops)} operations). Tweak the "
                "plan or ask the AI to modify it, then press Build.")
            self._set_status(f"Template '{label}' loaded - press Build.")

    def _on_save_template(self):
        ops = self._current_plan_ops()
        if ops is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Save template", "Template name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            path = templates.save_user_template(name, ops)
        except (schema.SchemaError, ValueError, OSError) as exc:
            self._log_error(f"Could not save template: {exc}")
            return
        self._reload_templates()
        self._log_system(f"Saved template '{name}' → {path}")
        self._set_status("Template saved.")

    def _current_plan_ops(self):
        """The current plan as a validated op list, or None (status explains)."""
        if self._current_mode() == "engineering":
            if not self.eng.program:
                self._set_status("Timeline is empty - nothing to save.", error=True)
                return None
            return list(self.eng.program)
        text = self.preview.toPlainText().strip()
        if not text:
            self._set_status("No plan to save - generate or load one first.",
                             error=True)
            return None
        try:
            return schema.validate_program(json.loads(text))
        except json.JSONDecodeError as exc:
            self._log_error(f"Plan is not valid JSON: {exc}")
            return None
        except schema.SchemaError as exc:
            self._log_error(f"Plan is not a valid program: {exc}")
            return None

    # ------------------------------------------------------------------ #
    # Plan table
    # ------------------------------------------------------------------ #
    def _refresh_plan_table(self):
        """Redraw the table from whatever is in the plan box at this instant.

        Driven by the box's own ``textChanged``, so it tracks a generated plan,
        a loaded template and a hand edit through one path. A plan that will
        not parse is reported rather than left stale - a table showing the
        previous plan beside JSON that no longer says that is a lie the user
        would act on.
        """
        if self._current_mode() != "structured":
            return
        text = self.preview.toPlainText().strip()
        if not text:
            self.plan_table.set_message("The plan appears here, one row per step.")
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            self.plan_table.set_message(
                f"Not valid JSON yet - {exc.msg}, line {exc.lineno}.")
            return
        operations = describe.operations_of(data)
        if not operations:
            self.plan_table.set_message("This plan has no operations.")
            return
        # Describe first, validate second: a plan the validator rejects is
        # exactly when seeing it laid out helps most, so the complaint goes
        # underneath the rows instead of replacing them.
        note = ""
        try:
            schema.validate_program(data)
        except schema.SchemaError as exc:
            note = f"Will not build: {exc}"
        self.plan_table.set_rows(
            describe.plan_rows(operations, self.units_combo.currentText()),
            note=note)

    # ------------------------------------------------------------------ #
    # Editing the plan through the table
    # ------------------------------------------------------------------ #
    def _read_plan(self):
        """The plan box as ``(data, operations)``, or ``(None, None)``.

        Both are needed on the way back out: a plan can be a bare list or a
        ``{"operations": [...]}`` object, and rewriting one shape as the other
        would quietly discard whatever else the object carried.
        """
        text = self.preview.toPlainText().strip()
        if not text:
            return None, None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None, None
        operations = describe.operations_of(data)
        return (data, operations) if operations else (None, None)

    def _write_plan(self, data, operations) -> None:
        """Put an edited program back in the plan box.

        Setting the text is the whole update: ``textChanged`` redraws the table
        from it, so the table cannot drift from the JSON even for a frame.
        """
        if isinstance(data, dict):
            data["operations"] = operations
        else:
            data = operations
        self.preview.setPlainText(json.dumps(data, indent=2))

    def _edit_plan_step(self, step: int) -> None:
        """Open step ``step`` in a form of typed controls."""
        data, operations = self._read_plan()
        if operations is None or not (0 <= step < len(operations)):
            return

        # Only names defined *earlier* can be referred to by this step, so the
        # dialog offers exactly those - which is also what stops an edit
        # creating a reference to something built after it.
        defined = [op.get("name") for op in operations[:step]
                   if isinstance(op, dict) and op.get("name")]

        edited = op_form.edit_op(operations[step], defined_names=defined,
                                 parent=self)
        if edited is None:
            if isinstance(operations[step], dict) and \
                    operations[step].get("op") not in schema.OPERATIONS:
                self._set_status(
                    f"This build has no form for '{operations[step].get('op')}'"
                    " - edit it in the plan below.", error=True)
            return

        operations[step] = edited
        self._write_plan(data, operations)

    def _remove_plan_step(self, step: int) -> None:
        """Delete step ``step``, after asking."""
        data, operations = self._read_plan()
        if operations is None or not (0 <= step < len(operations)):
            return

        op = operations[step]
        label = op.get("name") or op.get("op") if isinstance(op, dict) else "?"
        answer = QtWidgets.QMessageBox.question(
            self, "Delete step",
            f"Delete step {step + 1}, '{label}'?\n\n"
            "Any later step that used it will need editing too.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return

        del operations[step]
        self._write_plan(data, operations)

    # ------------------------------------------------------------------ #
    # Shared generation context + worker
    # ------------------------------------------------------------------ #
    def _gen_context(self) -> dict:
        provider = self._current_provider()
        if provider.id == "openai":
            provider.endpoint = self.cfg.openai_endpoint()
        if provider.id == "localserver":
            provider.base_url = self.cfg.localserver_base_url()
        model = self.model_combo.currentText().strip()
        if provider.id == "machine":
            provider.base_url = self.cfg.machine_base_url()
            provider.model_path = self.cfg.machine_model_path()
            # The dropdown shows a filename (or a placeholder) for information;
            # neither is a model id the server would recognise.
            model = ""
        return {
            "provider": provider,
            "api_key": self.cfg.api_key(provider.id),
            "model": model or provider.default_model,
            "units": self.units_combo.currentText(),
            "temperature": self.cfg.temperature(),
            "max_tokens": self.cfg.max_tokens(),
            "thinking_level": self.cfg.thinking_level(),
            "print_profile": self.cfg.print_profile(),
            "part_layout": self._current_part_layout(),
            # "" means "use the built-in prompt"; engine treats it as unset.
            "system_prompt": self.cfg.system_prompt(self._current_mode()),
        }

    def run_worker(self, fn, on_success, on_error=None):
        self._set_busy(True)
        self._worker = LLMWorker(fn, self)
        self._worker.succeeded.connect(on_success)
        self._worker.failed.connect(on_error or self._on_failed)
        self._worker.finished.connect(lambda: self._set_busy(False))
        self._worker.start()

    # ------------------------------------------------------------------ #
    # Casual generation
    # ------------------------------------------------------------------ #
    def _on_generate(self):
        if self._current_mode() == "engineering":
            self.eng.add_ai()
            return
        description = self.input.toPlainText().strip()
        if not description:
            self._set_status("Type a description first.", error=True)
            return
        self._repair.reset(self.cfg.repair_rounds())
        # Keep the user's own words: every later round sends a repair or review
        # prompt instead, and the review needs the request it is checking.
        self._original_request = description
        self._review_of = None
        self._start_generation(description, log_as_user=True)

    def _start_generation(self, user_message, log_as_user=True):
        ctx = self._gen_context()
        if ctx["provider"].requires_key and not ctx["api_key"]:
            self._set_status(f"No API key for {ctx['provider'].label}.", error=True)
            self._log_error(f"No API key set for {ctx['provider'].label}. Open Settings (⚙).")
            return
        if not self._local_model_ready(ctx["provider"]):
            return
        if log_as_user:
            self._log_user(user_message)
        self._pending_user = user_message
        self._set_status(f"Asking {ctx['provider'].label} ({ctx['model']})…")
        fn = partial(
            engine.generate, ctx["provider"], ctx["api_key"], ctx["model"],
            user_message, list(self.history),
            mode=self._current_mode(), units=ctx["units"], temperature=ctx["temperature"],
            max_tokens=ctx["max_tokens"], thinking_level=ctx["thinking_level"],
            print_profile=ctx["print_profile"], part_layout=ctx["part_layout"],
            system_prompt=ctx["system_prompt"],
        )
        self.run_worker(fn, self._on_generated)

    def _local_model_ready(self, provider) -> bool:
        """Say plainly that no local model is chosen, before spending a request."""
        if provider.id != "machine" or self.cfg.machine_model_path():
            return True
        from ..llm import backend

        if backend.is_serving(self.cfg.machine_base_url()):
            return True  # attaching to a server someone else started
        self._set_status("No local model chosen.", error=True)
        self._log_error(
            "No local model chosen. Open Settings (…) → Local (Machine "
            "Activation) → Model → Choose… and pick a .gguf file.")
        return False

    def _on_generated(self, result):
        self.last_result = result
        self.show_reasoning(result)
        self.history.append({"role": "user", "content": self._pending_user})
        self.history.append({"role": "assistant", "content": result.raw})
        if len(self.history) > _HISTORY_MAX:
            del self.history[: len(self.history) - _HISTORY_MAX]

        if result.program is not None:
            self.preview.setPlainText(json.dumps({"operations": result.program}, indent=2))
            note = " (auto-repaired)" if result.repaired else ""
            self._log_system(f"Generated a plan with {len(result.program)} operation(s){note}.")
            for fix in getattr(result, "notes", ()):
                self._log_system(f"note: {fix}")
        else:
            self.preview.setPlainText(result.code or "")
            self._log_system(f"Generated Python ({len((result.code or '').splitlines())} lines).")

        self.build_btn.setEnabled(True)
        self.output_tabs.setCurrentIndex(1)
        self.input.clear()

        payload = result.program if result.program is not None else (result.code or "")

        # A review round asked "is this right?", not "fix this". An unchanged
        # program is the model signing the build off, so stop here rather than
        # rebuilding identical geometry.
        if self._review_of is not None:
            reviewed, self._review_of = self._review_of, None
            if harness.fingerprint(payload) == reviewed:
                self._log_system("The model reviewed the result and confirmed "
                                 "it matches the request.")
                self._set_status("Done - reviewed.")
                return
            self._log_system("The review returned a revised plan.")

        # Loop guard: during a repair round, a plan identical to one that
        # already failed will just fail again - stop instead of burning tokens.
        if self._repair.attempts and self._repair.seen_failure(payload):
            self._log_error(
                "The model returned the same failing plan again - stopping "
                "auto-repair. Edit the plan or refine your prompt.")
            self._set_status("Auto-repair stalled.", error=True)
            return

        if self.cfg.auto_run():
            self._build_from_preview()
        else:
            self._set_status("Review the plan, then press Build.")

    def _on_failed(self, message):
        # A review that never produced a reply cannot sign anything off; drop it
        # so a later plan is not mistaken for the reviewed one.
        self._review_of = None
        self._log_error(message)
        if self._try_plan_repair(message):
            return
        self._set_status("Generation failed.", error=True)

    def _try_plan_repair(self, message) -> bool:
        """Send a plan that failed validation back to the model, within budget.

        The repair budget used to cover only failures *after* a plan arrived -
        build errors and defective geometry. A plan rejected by the validator
        (a 3D point where a 2D one belongs, a scalar where a list belongs, a name
        reused) got one attempt inside the engine and then surfaced raw, which is
        the most common failure by far on a small local model with no grammar to
        keep it honest.
        """
        if self._current_mode() != "structured" or not self._repair.can_retry():
            return False
        error = getattr(self._worker, "error", None)
        if not harness.is_model_output_error(error):
            return False  # a bad key or a dead network is not the model's to fix
        self._repair.start_attempt()
        self._set_status(
            f"Invalid plan - asking the model to fix it (round {self._repair.round_label})…")
        self._log_system("Sending the validation error back to the model for a fix…")
        # Echo the rejected reply back when the engine attached it: a reply that
        # never validated is not in the history, so without this the model would
        # be asked to fix a program it cannot see.
        failed_reply = getattr(error, "raw_reply", "") or ""
        self._start_generation(
            prompts.repair_prompt(message, failed_reply=failed_reply),
            log_as_user=False)
        return True

    # ------------------------------------------------------------------ #
    # Building geometry (main thread)
    # ------------------------------------------------------------------ #
    def _on_build_clicked(self):
        self._repair.reset(self.cfg.repair_rounds())
        # An explicit Build click means the user has seen (and could edit) the
        # code in the preview; only then may the Python deny-list be skipped.
        self._build_from_preview(user_reviewed=True)

    def _build_from_preview(self, user_reviewed=False):
        mode = self._current_mode()
        text = self.preview.toPlainText().strip()
        if not text:
            self._set_status("Nothing to build.", error=True)
            return
        if mode == "python":
            self._build_python(text, user_reviewed)
        else:
            self._build_structured(text)

    def _build_structured(self, text):
        from ..cad import schema, interpreter  # lazy: needs FreeCAD
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            self._set_status("Plan is not valid JSON.", error=True)
            self._log_error(f"Plan JSON error: {exc}")
            return
        # Fingerprint the ops list itself so it matches result.program in the
        # loop guard (the preview text wraps it in {"operations": ...}).
        ops = data.get("operations", data) if isinstance(data, dict) else data
        try:
            ops = schema.validate_program(data)
            result_obj, objects, log_lines = interpreter.build_program(
                ops, group_separate=(self._current_part_layout() == "separate"))
        except (schema.SchemaError, interpreter.InterpreterError) as exc:
            self._handle_build_error(str(exc), failed_payload=ops)
            return
        for line in log_lines:
            if line.startswith("note:"):  # deterministic in-build corrections
                self._log_system(line)
        name = getattr(result_obj, "Label", None) or getattr(result_obj, "Name", "result")
        self._log_system(f"Built {len(log_lines)} step(s). Result object: '{name}'.")

        leaves, problems = self._inspect_program(ops, objects)
        if problems and self._try_geometry_repair(problems, ops, leaves):
            return  # defective build undone; a corrected plan is on its way
        if problems:
            self._log_error("Geometry warnings: " + "; ".join(problems))
            self._set_status("Built with geometry warnings.", error=True)
        else:
            self._set_status("Done.")
        self._post_build(result_obj, inspected=True)
        # Finish settling the build (including any bed-fit prompt) before
        # starting a review request. Sound geometry has nothing to repair, so
        # only a measurement that disagrees with the request earns a round-trip;
        # a clean build that matches the request costs nothing.
        if not problems:
            self._try_review(ops, leaves)

    def _build_python(self, text, user_reviewed=False):
        from ..cad import pyrun  # lazy: needs FreeCAD
        try:
            # Auto-run executes model output the user never looked at, so the
            # deny-list must stay on for it; a blocked build feeds the refusal
            # into the normal repair loop instead of running the code.
            log_lines, _code = pyrun.run_python_code(text, prechecked=user_reviewed)
        except pyrun.PythonRunError as exc:
            self._handle_build_error(str(exc), failed_payload=text)
            return
        for line in log_lines:
            self._log_system(line)
        result = None
        try:
            import FreeCAD as App
            if App.ActiveDocument and App.ActiveDocument.Objects:
                result = App.ActiveDocument.Objects[-1]
        except Exception:
            pass
        self._set_status("Done.")
        self._post_build(result)

    def _handle_build_error(self, message, failed_payload=None):
        self._log_error(f"Build failed: {message}")
        mode = self._current_mode()
        if mode not in ("structured", "python") or not self._repair.can_retry():
            self._set_status("Build failed. Edit the plan or refine your prompt.", error=True)
            return
        if failed_payload is not None:
            self._repair.note_failure(failed_payload)
        self._repair.start_attempt()
        rounds = self._repair.round_label
        self._set_status(f"Build failed - asking the model to fix it (round {rounds})…")
        self._log_system(f"Sending the error back to the model for a fix (round {rounds})…")
        prompt = (prompts.python_repair_prompt(message) if mode == "python"
                  else prompts.repair_prompt(message))
        self._start_generation(prompt, log_as_user=False)

    # ------------------------------------------------------------------ #
    # Post-build: bed-fit check + export enablement (shared)
    # ------------------------------------------------------------------ #
    def _post_build(self, result_obj, inspected=False):
        self._last_built = result_obj
        self.export_btn.setEnabled(result_obj is not None)
        self.step_btn.setEnabled(result_obj is not None)
        if not inspected:
            problems = self._inspect_built(result_obj)
            if problems:
                self._log_error("Geometry warnings: " + "; ".join(problems))
        self._fit_view()
        if result_obj is not None and self.cfg.print_mode():
            self._check_bed_fit(result_obj)

    def _inspect_built(self, obj):
        """Inspect a built object, log a one-line report, return problem strings."""
        if obj is None:
            return []
        from ..cad import inspect as ginspect
        try:
            facts = ginspect.inspect_object(obj)
        except Exception:
            return []
        expect_single = (self._current_mode() != "python"
                         and self._current_part_layout() == "fused")
        self._log_system(ginspect.summary(facts))
        return ginspect.problems(facts, expect_single=expect_single)

    def _inspect_program(self, ops, objects):
        """Inspect every end product of a program, not just the last object.

        Returns ``(leaves, problems)``. The last operation's result can be a
        perfectly healthy solid while the program has quietly left other solids
        beside it - those are exactly what inspecting one object cannot see.
        """
        from ..cad import inspect as ginspect, schema
        try:
            names = schema.leaf_names(ops)
            leaves = ginspect.inspect_leaves(objects, names)
        except Exception:
            return [], []
        for facts in leaves:
            self._log_system(ginspect.summary(facts))
        expect_single = self._current_part_layout() == "fused"
        return leaves, ginspect.program_problems(leaves, expect_single=expect_single)

    def _try_geometry_repair(self, problems, program, leaves=None):
        """Undo a defective structured build and ask the model for a fix.

        Returns True if a repair round-trip was started (shares the repair
        budget with schema/build repairs).
        """
        if self._current_mode() != "structured" or not self._repair.can_retry():
            return False
        from ..cad import inspect as ginspect
        self._repair.note_failure(program)
        self._repair.start_attempt()
        report = "; ".join(problems)
        self._log_error(f"Geometry check failed: {report}")
        try:
            import FreeCAD as App
            if App.ActiveDocument is not None and App.ActiveDocument.UndoCount > 0:
                App.ActiveDocument.undo()
                self._log_system("Undid the defective build.")
        except Exception:
            pass
        self._set_status(
            f"Defective geometry - asking the model to fix it (round {self._repair.round_label})…")
        self._start_generation(
            prompts.geometry_repair_prompt(
                report, measurements=ginspect.measurement_table(leaves or [])),
            log_as_user=False)
        return True

    def _try_review(self, program, leaves):
        """Ask the model to check a sound build against the original request.

        Fires only when a measurement disagrees with what was asked for, so a
        build that matches the request costs nothing. Unlike a repair this does
        not undo anything: the geometry is valid, and the model may well
        confirm it is correct - :meth:`_on_generated` treats an unchanged
        program as a sign-off.
        """
        if self._current_mode() != "structured" or not self._repair.can_retry():
            return False
        if not self._original_request:
            return False
        from ..cad import inspect as ginspect
        concern = ginspect.dimension_check(self._original_request, leaves)
        if not concern:
            return False
        self._review_of = harness.fingerprint(program)
        self._repair.start_attempt()
        self._log_system(f"Checking the build against the request: {concern}.")
        self._set_status(
            f"Reviewing the result (round {self._repair.round_label})…")
        self._start_generation(
            prompts.review_prompt(self._original_request, concern,
                                  ginspect.measurement_table(leaves)),
            log_as_user=False)
        return True

    def _check_bed_fit(self, obj):
        from ..cad import export
        try:
            dims = export.bbox(obj)
        except Exception as exc:
            self._log_error(f"Could not measure bounding box: {exc}")
            return
        bed = [self.cfg.bed_x(), self.cfg.bed_y(), self.cfg.bed_z()]
        dim_s = f"{dims[0]:.1f}×{dims[1]:.1f}×{dims[2]:.1f} mm"
        if any(export.overage(dims, bed)):
            self._log_error(
                f"Part {dim_s} exceeds the "
                f"{bed[0]:.0f}×{bed[1]:.0f}×{bed[2]:.0f} mm build volume.")
            ret = QtWidgets.QMessageBox.question(
                self, "Exceeds build volume",
                f"The part ({dim_s}) is larger than the print bed.\nScale it to fit?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if ret == QtWidgets.QMessageBox.Yes:
                try:
                    self._last_built = export.scale_to_fit(obj, bed)
                    self._log_system("Scaled the part to fit the build volume.")
                    self._fit_view()
                except Exception as exc:
                    self._log_error(f"Scale failed: {exc}")
        else:
            self._log_system(f"Part {dim_s} fits the build volume.")

    def _on_export_stl(self):
        if self._last_built is None:
            self._set_status("Build something first.", error=True)
            return
        from ..cad import export
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export STL", "", "STL files (*.stl)")
        if not path:
            return
        if not path.lower().endswith(".stl"):
            path += ".stl"
        try:
            export.export_stl(self._last_built, path, self.cfg.stl_deflection())
            self._log_system(f"Exported STL → {path}")
            self._set_status("STL exported.")
        except Exception as exc:
            self._log_error(f"STL export failed: {exc}")
            self._set_status("STL export failed.", error=True)

    def _on_export_step(self):
        if self._last_built is None:
            self._set_status("Build something first.", error=True)
            return
        from ..cad import export
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export STEP", "", "STEP files (*.step *.stp)")
        if not path:
            return
        if not path.lower().endswith((".step", ".stp")):
            path += ".step"
        try:
            export.export_step(self._last_built, path)
            self._log_system(f"Exported STEP → {path}")
            self._set_status("STEP exported.")
        except Exception as exc:
            self._log_error(f"STEP export failed: {exc}")
            self._set_status("STEP export failed.", error=True)

    # ------------------------------------------------------------------ #
    # Misc actions
    # ------------------------------------------------------------------ #
    def _on_undo(self):
        try:
            import FreeCAD as App
        except ImportError:
            return
        doc = App.ActiveDocument
        if doc is not None and doc.UndoCount > 0:
            doc.undo()
            self._log_system("Undid the last action.")
            self._fit_view()
        else:
            self._set_status("Nothing to undo.", error=True)

    def _on_clear(self):
        self.history = []
        self.last_result = None
        self._last_built = None
        self.log.clear()
        self.preview.clear()
        self.thinking.clear()
        self.usage_label.setVisible(False)
        self.input.clear()
        self.build_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.step_btn.setEnabled(False)
        self.eng.clear()
        self._set_status("Cleared.")

    def _fit_view(self):
        try:
            import FreeCAD as App
            import FreeCADGui as Gui
            if App.ActiveDocument:
                App.ActiveDocument.recompute()
            Gui.SendMsgToActiveView("ViewFit")
            Gui.activeDocument().activeView().viewAxonometric()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # UI helpers
    # ------------------------------------------------------------------ #
    def _generate_idle_label(self):
        return "Add step" if self._current_mode() == "engineering" else "Generate"

    def _set_busy(self, busy):
        self.generate_btn.setEnabled(not busy)
        self.provider_combo.setEnabled(not busy)
        self.model_combo.setEnabled(not busy)
        self.generate_btn.setText("Thinking…" if busy else self._generate_idle_label())

    def _set_status(self, text, error=False):
        self.status.setText(text)
        self._status_error = error
        self.status.setStyleSheet(
            f"color: {theme.danger(self) if error else theme.muted(self)};")

    def _append(self, html):
        self.log.append(html)
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _log_user(self, text):
        self._append(f'<p style="margin:2px 0;"><b>You:</b> {_esc(text)}</p>')

    def _log_system(self, text):
        # Deliberately uncoloured: the theme's own text colour is the only one
        # guaranteed to be readable on the theme's own background.
        self._append(f'<p style="margin:2px 0;">{_esc(text)}</p>')

    def _log_error(self, text):
        self._append(f'<p style="margin:2px 0; color:{theme.danger(self.log)};">'
                     f'<b>Error:</b> {_esc(text)}</p>')
        self.output_tabs.setCurrentIndex(0)


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("\n", "<br>"))


# --------------------------------------------------------------------------- #
# Dock management
# --------------------------------------------------------------------------- #
def show_panel():
    """Create (or raise) the GPT4FreeCAD dock widget in the FreeCAD main window."""
    import FreeCADGui as Gui

    mw = Gui.getMainWindow()
    existing = mw.findChild(QtWidgets.QDockWidget, "GPT4FreeCADDock")
    if existing is not None and existing.property("GPT4FreeCADUIVersion") != _UI_VERSION:
        existing.close()
        existing.setParent(None)
        existing.deleteLater()
        QtWidgets.QApplication.processEvents()
        existing = None

    if existing is not None:
        side_areas = QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea
        existing.setAllowedAreas(side_areas)
        existing.setFloating(False)
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, existing)
        existing.setMinimumSize(130, 105)
        existing.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
        if existing.widget() is not None:
            existing.widget().setMinimumSize(120, 86)
        existing.show()
        existing.raise_()
        return existing

    dock = QtWidgets.QDockWidget("GPT4FreeCAD", mw)
    dock.setObjectName("GPT4FreeCADDock")
    dock.setProperty("GPT4FreeCADUIVersion", _UI_VERSION)
    dock.setAllowedAreas(
        QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
    dock.setFloating(False)
    dock.setMinimumSize(130, 105)
    dock.setSizePolicy(
        QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
    dock.setWidget(GPTPanel())
    mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    dock.show()
    dock.raise_()
    return dock
