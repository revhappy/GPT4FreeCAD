"""Settings dialog: API keys, per-provider models, and generation parameters."""

from __future__ import annotations

import os

from .qt import QtCore, QtGui, QtWidgets, exec_dialog
from .worker import LLMWorker
from ..config import get_config
from ..llm import all_providers, get_provider, ChatRequest
from ..llm.base import LLMError


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cfg = get_config()
        self._key_edits = {}
        self._model_combos = {}
        self.setWindowTitle("GPT4FreeCAD - Settings")
        self.resize(560, 600)
        self._build()

    # ------------------------------------------------------------------ #
    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "Enter an API key for each provider you want to use. Keys are stored "
            "in your FreeCAD preferences (not in plain-text files)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        scroll.setWidget(inner)
        form_host = QtWidgets.QVBoxLayout(inner)
        layout.addWidget(scroll, 1)

        for provider in all_providers():
            form_host.addWidget(self._provider_group(provider))

        form_host.addWidget(self._general_group())
        form_host.addWidget(self._printing_group())
        form_host.addStretch(1)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _provider_group(self, provider):
        box = QtWidgets.QGroupBox(provider.label)
        form = QtWidgets.QFormLayout(box)

        if provider.requires_key:
            key_edit = QtWidgets.QLineEdit(self.cfg.api_key(provider.id))
            key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
            key_edit.setPlaceholderText("Paste API key")
            self._key_edits[provider.id] = key_edit

            show = QtWidgets.QCheckBox("Show")
            show.toggled.connect(
                lambda on, e=key_edit: e.setEchoMode(
                    QtWidgets.QLineEdit.Normal if on else QtWidgets.QLineEdit.Password
                )
            )
            key_row = QtWidgets.QHBoxLayout()
            key_row.addWidget(key_edit, 1)
            key_row.addWidget(show)
            key_wrap = QtWidgets.QWidget()
            key_wrap.setLayout(key_row)
            form.addRow("API key:", key_wrap)
        else:
            note = QtWidgets.QLabel(
                "Runs on this machine — no API key, no cloud, works offline. "
                "<b>Pick a model file below and you're done;</b> GPT4FreeCAD "
                "starts and stops it for you."
            )
            note.setWordWrap(True)
            form.addRow("", note)

        if provider.id == "machine":
            self._machine_model = QtWidgets.QLineEdit(self.cfg.machine_model_path())
            self._machine_model.setPlaceholderText("Choose a .gguf model file…")
            self._machine_model.setToolTip(
                "The local model to run. GPT4FreeCAD loads it on first use - "
                "no terminal, no server to start.")
            browse = QtWidgets.QPushButton("Choose…")
            browse.setDefault(False)
            browse.setAutoDefault(False)
            browse.clicked.connect(self._pick_model)
            model_row = QtWidgets.QHBoxLayout()
            model_row.addWidget(self._machine_model, 1)
            model_row.addWidget(browse)
            model_wrap = QtWidgets.QWidget()
            model_wrap.setLayout(model_row)
            form.addRow("Model:", model_wrap)

            self._machine_status = QtWidgets.QLabel()
            self._machine_status.setWordWrap(True)
            self._machine_status.setStyleSheet("color: gray;")
            self._machine_unload = QtWidgets.QPushButton("Unload")
            self._machine_unload.setToolTip(
                "Stop the local model server and free its memory. It reloads "
                "automatically on the next Generate.")
            self._machine_unload.clicked.connect(self._unload_model)
            status_row = QtWidgets.QHBoxLayout()
            status_row.addWidget(self._machine_status, 1)
            status_row.addWidget(self._machine_unload)
            status_wrap = QtWidgets.QWidget()
            status_wrap.setLayout(status_row)
            form.addRow("", status_wrap)
            self._refresh_machine_status()

            # Plumbing almost nobody should touch: only relevant when attaching
            # to a server started elsewhere. A checkable group box stays greyed
            # out until it is ticked, so it reads as optional.
            advanced = QtWidgets.QGroupBox("Advanced — attach to a server I started myself")
            advanced.setCheckable(True)
            advanced.setChecked(False)
            advanced_form = QtWidgets.QFormLayout(advanced)
            self._machine_url = QtWidgets.QLineEdit(self.cfg.machine_base_url())
            self._machine_url.setToolTip(
                "Where the local server listens. Change this only to attach to a "
                "server you started yourself (default http://127.0.0.1:8177).")
            advanced_form.addRow("Server URL:", self._machine_url)
            form.addRow("", advanced)

        if provider.id != "machine":
            # The local provider's "model" is the .gguf picked above; a second
            # (empty) catalogue combo would only sit there confusing people.
            model_combo = QtWidgets.QComboBox()
            model_combo.setEditable(True)
            model_combo.addItems(provider.default_models)
            model_combo.setCurrentText(self.cfg.model(provider.id, provider.default_model))
            self._model_combos[provider.id] = model_combo
            form.addRow("Model:", model_combo)

        if provider.id == "openai":
            endpoint = QtWidgets.QLineEdit(self.cfg.openai_endpoint())
            endpoint.setToolTip(
                "Override for OpenAI-compatible gateways (Azure, OpenRouter, "
                "local servers). Leave as default for OpenAI."
            )
            self._endpoint_edit = endpoint
            form.addRow("Endpoint:", endpoint)

        bottom = QtWidgets.QHBoxLayout()
        link_text = ("Get an API key" if provider.requires_key
                     else "Machine Activation SDK")
        get_key = QtWidgets.QLabel(f'<a href="{provider.api_key_url}">{link_text}</a>')
        get_key.setOpenExternalLinks(True)
        bottom.addWidget(get_key)
        bottom.addStretch(1)
        test_btn = QtWidgets.QPushButton("Test connection")
        test_btn.clicked.connect(lambda _=False, p=provider: self._test(p))
        bottom.addWidget(test_btn)
        wrap = QtWidgets.QWidget()
        wrap.setLayout(bottom)
        form.addRow("", wrap)

        return box

    def _general_group(self):
        box = QtWidgets.QGroupBox("Generation")
        form = QtWidgets.QFormLayout(box)

        self._temp = QtWidgets.QDoubleSpinBox()
        self._temp.setRange(0.0, 2.0)
        self._temp.setSingleStep(0.1)
        self._temp.setValue(self.cfg.temperature())
        self._temp.setToolTip("Lower = more deterministic. 0.2 is a good default for CAD.")
        form.addRow("Temperature:", self._temp)

        self._max_tokens = QtWidgets.QSpinBox()
        self._max_tokens.setRange(256, 32768)
        self._max_tokens.setSingleStep(256)
        self._max_tokens.setValue(self.cfg.max_tokens())
        form.addRow("Max tokens:", self._max_tokens)

        self._auto_run = QtWidgets.QCheckBox("Build geometry automatically after generating")
        self._auto_run.setChecked(self.cfg.auto_run())
        self._auto_run.setToolTip(
            "If unchecked, the generated plan is shown for review and built only "
            "when you press Build."
        )
        form.addRow("", self._auto_run)

        self._repair_rounds = QtWidgets.QSpinBox()
        self._repair_rounds.setRange(0, 10)
        self._repair_rounds.setValue(self.cfg.repair_rounds())
        self._repair_rounds.setToolTip(
            "How many times a failed generation or build is automatically sent "
            "back to the model for a fix before giving up. 0 disables auto-repair."
        )
        form.addRow("Auto-repair rounds:", self._repair_rounds)

        return box

    def _printing_group(self):
        box = QtWidgets.QGroupBox("3D printing")
        form = QtWidgets.QFormLayout(box)

        def _bed_spin(value):
            sb = QtWidgets.QDoubleSpinBox()
            sb.setRange(10.0, 2000.0)
            sb.setDecimals(1)
            sb.setSuffix(" mm")
            sb.setValue(value)
            return sb

        self._bed_x = _bed_spin(self.cfg.bed_x())
        self._bed_y = _bed_spin(self.cfg.bed_y())
        self._bed_z = _bed_spin(self.cfg.bed_z())
        bed_row = QtWidgets.QHBoxLayout()
        for sb in (self._bed_x, self._bed_y, self._bed_z):
            bed_row.addWidget(sb)
        bed_wrap = QtWidgets.QWidget()
        bed_wrap.setLayout(bed_row)
        bed_wrap.setToolTip("Printer build volume (X × Y × Z). 254 mm = 10 inches.")
        form.addRow("Build volume:", bed_wrap)

        self._deflection = QtWidgets.QDoubleSpinBox()
        self._deflection.setRange(0.001, 5.0)
        self._deflection.setDecimals(3)
        self._deflection.setSingleStep(0.05)
        self._deflection.setSuffix(" mm")
        self._deflection.setValue(self.cfg.stl_deflection())
        self._deflection.setToolTip(
            "STL mesh deviation: max distance between the mesh and the true surface. "
            "Smaller = finer mesh, larger file.")
        form.addRow("STL mesh deviation:", self._deflection)

        return box

    # ------------------------------------------------------------------ #
    def _test(self, provider):
        if not provider.requires_key:
            self._test_local(provider)
            return
        key = self._key_edits[provider.id].text().strip()
        model = self._model_combos[provider.id].currentText().strip()
        if not key:
            QtWidgets.QMessageBox.warning(self, "Test", "Enter an API key first.")
            return
        if provider.id == "openai" and hasattr(self, "_endpoint_edit"):
            get_provider("openai").endpoint = self._endpoint_edit.text().strip() or \
                get_provider("openai").endpoint

        QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.WaitCursor))
        try:
            # Reasoning models (Claude 5, gpt-5.x, Gemini 3) think before they
            # answer, and the thinking spends the same output budget - a tiny
            # cap makes a working key look broken. 512 covers the thinking.
            reply = provider.chat(
                ChatRequest(
                    messages=[{"role": "user", "content": "Reply with the single word: OK"}],
                    model=model, max_tokens=512, temperature=0.0,
                ),
                key,
            )
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.information(
                self, "Test", f"{provider.label} responded:\n\n{reply.strip()[:200]}"
            )
        except LLMError as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "Test failed", str(exc))
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "Test failed", str(exc))

    def _pick_model(self):
        """Native file dialog for a .gguf. The only step a local model needs."""
        start = self._machine_model.text().strip() or ""
        start_dir = os.path.dirname(start) if start else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose a local model", start_dir,
            "Model files (*.gguf *.mcart);;All files (*)")
        if path:
            self._machine_model.setText(path)
            self._refresh_machine_status()

    def _refresh_machine_status(self):
        """One line on what will happen when Generate is pressed."""
        from ..llm import local as local_mod

        model = self._machine_model.text().strip()
        running = local_mod.activated_model()
        if running:
            self._machine_status.setText(f"Model loaded and serving at {running}.")
        elif model:
            name = os.path.basename(model)
            if os.path.isfile(model):
                size = os.path.getsize(model) / (1024 ** 3)
                self._machine_status.setText(
                    f"{name} ({size:.1f} GB) — loads automatically on first use.")
            else:
                self._machine_status.setText(f"File not found: {model}")
        else:
            self._machine_status.setText(
                "No model chosen. Pick a .gguf file, or tick Advanced to attach "
                "to a server you started yourself.")

    def _test_local(self, provider):
        """Report the activation contract: does this model fit, on what hardware.

        A chat round-trip would also work but can take a minute while a local
        model loads; the activation report answers the useful question instantly.
        Loading the weights is the slow part, so it runs on a worker thread
        behind a busy dialog - on the GUI thread it would freeze FreeCAD for
        however long the model takes to load.
        """
        if hasattr(self, "_machine_url"):
            provider.base_url = self._machine_url.text().strip() or provider.base_url
        provider.model_path = self._machine_model.text().strip()

        progress = QtWidgets.QProgressDialog(
            "Loading the local model (first load can take a while)…", "", 0, 0, self)
        progress.setCancelButton(None)
        progress.setWindowTitle("Local model")
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        def job():
            provider.activate()
            return provider.activation_summary(), provider.list_models()

        def on_ok(result):
            summary, models = result
            self._refresh_machine_status()
            if models:
                summary += f"\n\nLoaded model(s): {', '.join(models)}"
                QtWidgets.QMessageBox.information(self, "Local model", summary)
            else:
                QtWidgets.QMessageBox.warning(self, "Local model", summary)

        def on_fail(message):
            self._refresh_machine_status()
            QtWidgets.QMessageBox.critical(self, "Local model", message)

        self._test_worker = LLMWorker(job, self)
        self._test_worker.succeeded.connect(on_ok)
        self._test_worker.failed.connect(on_fail)
        self._test_worker.finished.connect(progress.close)
        self._test_worker.start()

    def _unload_model(self):
        """Stop a local server this session started and free its memory."""
        from ..llm import local as local_mod

        if local_mod.deactivate_model():
            self._machine_status.setText(
                "Model unloaded - it reloads on the next Generate.")
        else:
            # Nothing we started is running; just re-state the current facts.
            self._refresh_machine_status()

    def _save(self):
        if hasattr(self, "_machine_url"):
            self.cfg.set_machine_base_url(self._machine_url.text())
        if hasattr(self, "_machine_model"):
            self.cfg.set_machine_model_path(self._machine_model.text())
        for pid, edit in self._key_edits.items():
            self.cfg.set_api_key(pid, edit.text())
        for pid, combo in self._model_combos.items():
            self.cfg.set_model(pid, combo.currentText().strip())
        if hasattr(self, "_endpoint_edit"):
            self.cfg.set_openai_endpoint(self._endpoint_edit.text().strip())
        self.cfg.set_temperature(self._temp.value())
        self.cfg.set_max_tokens(self._max_tokens.value())
        self.cfg.set_auto_run(self._auto_run.isChecked())
        self.cfg.set_repair_rounds(self._repair_rounds.value())
        self.cfg.set_bed(self._bed_x.value(), self._bed_y.value(), self._bed_z.value())
        self.cfg.set_stl_deflection(self._deflection.value())
        self.accept()


def open_settings(parent=None) -> bool:
    """Open the settings dialog modally. Returns True if saved."""
    dialog = SettingsDialog(parent)
    return exec_dialog(dialog) == QtWidgets.QDialog.Accepted
