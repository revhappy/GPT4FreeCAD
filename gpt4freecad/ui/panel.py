"""The dockable GPT4FreeCAD panel - the primary interface."""

from __future__ import annotations

import json
from functools import partial

from .qt import QtCore, QtGui, QtWidgets
from .worker import LLMWorker
from .settings import open_settings
from .engineering import EngineeringWidget
from ..config import get_config
from ..llm import all_providers, get_provider
from ..llm.base import LLMError
from .. import engine, harness
from ..cad import prompts, schema, templates

_SHAPE_HINTS = ["(auto)", "box", "cylinder", "sphere", "cone", "torus",
                "extruded profile", "assembly of primitives"]
_UNITS = ["mm", "cm", "m", "in"]
_MODES = [("Structured", "structured"),
          ("Engineering", "engineering"),
          ("Python", "python")]
_MODE_INDEX = {m[1]: i for i, m in enumerate(_MODES)}
# Gemini 3 reasoning depth. "Default" = let the model decide (don't send the param).
_THINKING = [("Default", "default"), ("Minimal", "minimal"), ("Low", "low"),
             ("Medium", "medium"), ("High", "high")]
_UI_VERSION = "compact-5"


class GPTPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cfg = get_config()
        self.history = []            # multi-turn conversation (user/assistant)
        self.last_result = None
        self._last_built = None      # last result object (for Export STL / scale)
        self._worker = None
        self._pending_user = ""
        self._repair = harness.RepairSession(self.cfg.repair_rounds())
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
        self.units_combo.currentTextChanged.connect(
            lambda text: None if self._loading else self.cfg.set_units(text))
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
        casual_layout.addWidget(self.preview, 1)
        self.stack.addWidget(casual)

        self.eng = EngineeringWidget(self)
        self.stack.addWidget(self.eng)
        self.output_tabs.addTab(self.stack, "Plan")
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
        self.status.setStyleSheet("color: gray;")
        self.status.setMaximumHeight(14)
        root.addWidget(self.status)

        self._reload_templates()

    def _build_legacy_ui(self):
        self.setMinimumSize(120, 86)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(3, 3, 3, 3)
        root.setSpacing(3)

        # Keep the prompt at the top. Even if the dock is partly below the
        # screen, the description and Generate action remain immediately
        # reachable instead of being trapped at the bottom.
        composer = QtWidgets.QWidget()
        composer.setMinimumSize(0, 0)
        composer_layout = QtWidgets.QVBoxLayout(composer)
        composer_layout.setContentsMargins(0, 0, 0, 0)
        composer_layout.setSpacing(2)

        self.input = QtWidgets.QPlainTextEdit()
        self.input.setPlaceholderText(
            "Describe the part or next engineering step...")
        self.input.setMinimumHeight(34)
        self.input.setMaximumHeight(48)
        composer_layout.addWidget(self.input)

        self.generate_btn = QtWidgets.QPushButton("Generate")
        self.generate_btn.setDefault(True)
        self.generate_btn.setMinimumHeight(26)
        self.generate_btn.setMaximumHeight(30)
        self.generate_btn.setToolTip("Generate a CAD plan from the description above.")
        self.generate_btn.clicked.connect(self._on_generate)
        composer_layout.addWidget(self.generate_btn)

        self.status = QtWidgets.QLabel("Ready.")
        self.status.setStyleSheet("color: gray;")
        self.status.setMaximumHeight(18)
        root.addWidget(composer)
        root.addWidget(self.status)

        # Everything else yields space and scrolls independently.
        content = QtWidgets.QWidget()
        content.setMinimumSize(0, 0)
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(3)

        # --- header: provider / model / settings ---
        header = QtWidgets.QGridLayout()
        header.addWidget(QtWidgets.QLabel("Provider:"), 0, 0)
        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.setMaximumHeight(24)
        for p in all_providers():
            self.provider_combo.addItem(p.label, p.id)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        header.addWidget(self.provider_combo, 0, 1)

        self.settings_btn = QtWidgets.QToolButton()
        self.settings_btn.setMaximumSize(26, 24)
        self.settings_btn.setText("⚙")
        self.settings_btn.setToolTip("Settings (API keys, models, 3D printing)")
        self.settings_btn.clicked.connect(self._open_settings)
        header.addWidget(self.settings_btn, 0, 2)

        header.addWidget(QtWidgets.QLabel("Model:"), 1, 0)
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setMaximumHeight(24)
        self.model_combo.setEditable(True)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        header.addWidget(self.model_combo, 1, 1, 1, 2)
        content_layout.addLayout(header)

        # --- mode + units + thinking + print ---
        mode_row = QtWidgets.QGridLayout()
        mode_row.addWidget(QtWidgets.QLabel("Mode:"), 0, 0)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.setMaximumHeight(24)
        for label, _ in _MODES:
            self.mode_combo.addItem(label)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_combo, 0, 1, 1, 3)

        self.units_label = QtWidgets.QLabel("Units:")
        mode_row.addWidget(self.units_label, 1, 0)
        self.units_combo = QtWidgets.QComboBox()
        self.units_combo.setMaximumHeight(24)
        self.units_combo.addItems(_UNITS)
        self.units_combo.currentTextChanged.connect(
            lambda t: None if self._loading else self.cfg.set_units(t))
        mode_row.addWidget(self.units_combo, 1, 1)

        self.thinking_label = QtWidgets.QLabel("Thinking:")
        mode_row.addWidget(self.thinking_label, 1, 2)
        self.thinking_combo = QtWidgets.QComboBox()
        self.thinking_combo.setMaximumHeight(24)
        for label, value in _THINKING:
            self.thinking_combo.addItem(label, value)
        self.thinking_combo.setToolTip(
            "Gemini 3 reasoning depth. Lower = faster/cheaper; higher = more careful.")
        self.thinking_combo.currentIndexChanged.connect(self._on_thinking_changed)
        mode_row.addWidget(self.thinking_combo, 1, 3)
        mode_row.setColumnStretch(1, 1)
        mode_row.setColumnStretch(3, 1)
        content_layout.addLayout(mode_row)

        self.print_check = QtWidgets.QCheckBox("3D-print mode (printability + bed fit + STL)")
        self.print_check.toggled.connect(self._on_print_toggled)
        content_layout.addWidget(self.print_check)

        # --- results log ---
        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(36)
        content_layout.addWidget(self.log, 1)

        # --- central stack: casual page (preview) vs engineering page ---
        self.stack = QtWidgets.QStackedWidget()

        casual = QtWidgets.QWidget()
        cv = QtWidgets.QVBoxLayout(casual)
        cv.setContentsMargins(0, 0, 0, 0)
        self.preview_label = QtWidgets.QLabel("Plan (JSON):")
        cv.addWidget(self.preview_label)
        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setPlaceholderText(
            "The generated plan / code appears here. You can edit it before pressing Build.")
        mono = QtGui.QFont("Consolas")
        mono.setFixedPitch(True)
        self.preview.setFont(mono)
        self.preview.setMinimumHeight(36)
        cv.addWidget(self.preview, 1)
        self.shape_row = QtWidgets.QWidget()
        sr = QtWidgets.QHBoxLayout(self.shape_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.addWidget(QtWidgets.QLabel("Shape:"))
        self.shape_combo = QtWidgets.QComboBox()
        self.shape_combo.setMaximumHeight(24)
        self.shape_combo.addItems(_SHAPE_HINTS)
        sr.addWidget(self.shape_combo, 1)
        cv.addWidget(self.shape_row)
        self.stack.addWidget(casual)                       # index 0

        self.eng = EngineeringWidget(self)
        self.stack.addWidget(self.eng)                     # index 1
        content_layout.addWidget(self.stack, 2)

        self.content_scroll = QtWidgets.QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.content_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.content_scroll.setMinimumSize(0, 0)
        self.content_scroll.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustIgnored)
        self.content_scroll.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
        self.content_scroll.setWidget(content)
        root.addWidget(self.content_scroll, 1)

        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(2)

        self.build_btn = QtWidgets.QPushButton("Build")
        self.build_btn.setToolTip("Build the geometry from the plan above.")
        self.build_btn.clicked.connect(self._on_build_clicked)
        self.build_btn.setEnabled(False)
        self.build_btn.setMaximumHeight(26)
        btns.addWidget(self.build_btn, 1)

        self.export_btn = QtWidgets.QPushButton("Export STL…")
        self.export_btn.setToolTip("Export the built result as an STL mesh.")
        self.export_btn.clicked.connect(self._on_export_stl)
        self.export_btn.setEnabled(False)
        self.export_btn.setMaximumHeight(26)
        btns.addWidget(self.export_btn, 1)

        self.undo_btn = QtWidgets.QPushButton("Undo")
        self.undo_btn.clicked.connect(self._on_undo)
        self.undo_btn.setMaximumHeight(26)
        btns.addWidget(self.undo_btn)

        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.clear_btn.clicked.connect(self._on_clear)
        self.clear_btn.setMaximumHeight(26)
        btns.addWidget(self.clear_btn)
        content_layout.addLayout(btns)

    # ------------------------------------------------------------------ #
    # State load / persistence
    # ------------------------------------------------------------------ #
    def _load_state(self):
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

        if not self._any_key_set():
            self._log_system(
                "Welcome to GPT4FreeCAD. Open Settings (⚙) to add an API key for Gemini, "
                "OpenAI, or Claude - or pick the 'Local (Machine Activation)' provider to "
                "run a model on this machine with no key and no cloud. Then describe a "
                "part below.")

    def _populate_models(self):
        provider = self._current_provider()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(provider.default_models)
        self.model_combo.setCurrentText(self.cfg.model(provider.id, provider.default_model))
        self.model_combo.blockSignals(False)

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
        if not self._loading:
            self.cfg.set_model(self.provider_combo.currentData(), text.strip())

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
        if layout == "separate":
            self._set_status("New parts will stay separate and editable.")
        else:
            self._set_status("New features will use the fused single-part workflow.")

    def _apply_mode_visibility(self, mode):
        is_struct = mode == "structured"
        is_eng = mode == "engineering"
        self.stack.setCurrentIndex(1 if is_eng else 0)
        self.units_label.setVisible(is_struct or is_eng)
        self.units_combo.setVisible(is_struct or is_eng)
        self.print_check.setVisible(is_struct or is_eng)
        self.part_layout_combo.setVisible(is_struct or is_eng)
        self.build_btn.setVisible(not is_eng)
        self.template_row.setVisible(is_struct or is_eng)
        self.preview_label.setText("Plan (JSON):" if is_struct else "Python code:")
        self.output_tabs.setTabText(1, "Steps" if is_eng else "Plan")
        self.generate_btn.setText(self._generate_idle_label())

    def _on_print_toggled(self, checked):
        if not self._loading:
            self.cfg.set_print_mode(checked)
        self._update_print_tooltip()

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
    # Shared generation context + worker
    # ------------------------------------------------------------------ #
    def _gen_context(self) -> dict:
        provider = self._current_provider()
        if provider.id == "openai":
            provider.endpoint = self.cfg.openai_endpoint()
        if provider.id == "machine":
            provider.base_url = self.cfg.machine_base_url()
        return {
            "provider": provider,
            "api_key": self.cfg.api_key(provider.id),
            "model": self.model_combo.currentText().strip() or provider.default_model,
            "units": self.units_combo.currentText(),
            "temperature": self.cfg.temperature(),
            "max_tokens": self.cfg.max_tokens(),
            "thinking_level": self.cfg.thinking_level(),
            "print_profile": self.cfg.print_profile(),
            "part_layout": self._current_part_layout(),
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
        self._start_generation(description, log_as_user=True)

    def _start_generation(self, user_message, log_as_user=True):
        ctx = self._gen_context()
        if ctx["provider"].requires_key and not ctx["api_key"]:
            self._set_status(f"No API key for {ctx['provider'].label}.", error=True)
            self._log_error(f"No API key set for {ctx['provider'].label}. Open Settings (⚙).")
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
        )
        self.run_worker(fn, self._on_generated)

    def _on_generated(self, result):
        self.last_result = result
        self.history.append({"role": "user", "content": self._pending_user})
        self.history.append({"role": "assistant", "content": result.raw})

        if result.program is not None:
            self.preview.setPlainText(json.dumps({"operations": result.program}, indent=2))
            note = " (auto-repaired)" if result.repaired else ""
            self._log_system(f"Generated a plan with {len(result.program)} operation(s){note}.")
        else:
            self.preview.setPlainText(result.code or "")
            self._log_system(f"Generated Python ({len((result.code or '').splitlines())} lines).")

        self.build_btn.setEnabled(True)
        self.output_tabs.setCurrentIndex(1)
        self.input.clear()

        # Loop guard: during a repair round, a plan identical to one that
        # already failed will just fail again - stop instead of burning tokens.
        payload = result.program if result.program is not None else (result.code or "")
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
        self._set_status("Generation failed.", error=True)
        self._log_error(message)

    # ------------------------------------------------------------------ #
    # Building geometry (main thread)
    # ------------------------------------------------------------------ #
    def _on_build_clicked(self):
        self._repair.reset(self.cfg.repair_rounds())
        self._build_from_preview()

    def _build_from_preview(self):
        mode = self._current_mode()
        text = self.preview.toPlainText().strip()
        if not text:
            self._set_status("Nothing to build.", error=True)
            return
        if mode == "python":
            self._build_python(text)
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
            result_obj, log_lines = interpreter.build_program(
                ops, group_separate=(self._current_part_layout() == "separate"))
        except (schema.SchemaError, interpreter.InterpreterError) as exc:
            self._handle_build_error(str(exc), failed_payload=ops)
            return
        for line in log_lines:
            if line.startswith("note:"):  # deterministic in-build corrections
                self._log_system(line)
        name = getattr(result_obj, "Label", None) or getattr(result_obj, "Name", "result")
        self._log_system(f"Built {len(log_lines)} step(s). Result object: '{name}'.")
        problems = self._inspect_built(result_obj)
        if problems and self._try_geometry_repair(problems, ops):
            return  # defective build undone; a corrected plan is on its way
        if problems:
            self._log_error("Geometry warnings: " + "; ".join(problems))
            self._set_status("Built with geometry warnings.", error=True)
        else:
            self._set_status("Done.")
        self._post_build(result_obj, inspected=True)

    def _build_python(self, text):
        from ..cad import pyrun  # lazy: needs FreeCAD
        try:
            log_lines, _code = pyrun.run_python_code(text, prechecked=True)
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

    def _try_geometry_repair(self, problems, program):
        """Undo a defective structured build and ask the model for a fix.

        Returns True if a repair round-trip was started (shares the repair
        budget with schema/build repairs).
        """
        if self._current_mode() != "structured" or not self._repair.can_retry():
            return False
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
        self._start_generation(prompts.geometry_repair_prompt(report), log_as_user=False)
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
        self.status.setStyleSheet("color: #c0392b;" if error else "color: gray;")

    def _append(self, html):
        self.log.append(html)
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _log_user(self, text):
        self._append(f'<p style="margin:2px 0;"><b>You:</b> {_esc(text)}</p>')

    def _log_system(self, text):
        self._append(f'<p style="margin:2px 0; color:#2c3e50;">{_esc(text)}</p>')

    def _log_error(self, text):
        self._append(f'<p style="margin:2px 0; color:#c0392b;"><b>Error:</b> {_esc(text)}</p>')
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
