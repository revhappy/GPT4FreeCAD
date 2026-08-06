"""Searchable model picker.

A combo box works for the five models a provider ships in its defaults. It does
not work for OpenRouter's 339, which is exactly the catalogue you most need to
search: the interesting model is usually one you have not heard of yet.

The dialog is provider-agnostic - it takes :class:`~gpt4freecad.llm.ModelInfo`
records and hands back an id. Filtering and formatting live in
:mod:`gpt4freecad.llm.base` so they can be tested without Qt.
"""

from __future__ import annotations

from typing import List, Optional

from .qt import QtCore, QtWidgets, exec_dialog


def _context_text(tokens: int) -> str:
    if not tokens:
        return "—"
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    return f"{tokens // 1000}k" if tokens >= 1000 else str(tokens)


def _price_text(model) -> str:
    if model.free:
        return "free"
    if not model.price_in and not model.price_out:
        return "—"
    return f"${model.price_in:.2f} / ${model.price_out:.2f}"


class ModelPicker(QtWidgets.QDialog):
    """Filterable table of models. ``chosen`` holds the id after accept."""

    def __init__(self, models: List, current: str = "", title: str = "Choose a model",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 480)
        self._models = list(models)
        self.chosen: Optional[str] = None

        layout = QtWidgets.QVBoxLayout(self)

        self._filter = QtWidgets.QLineEdit()
        self._filter.setPlaceholderText(
            "Filter by name or id — space-separated words all have to match")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        self._table = QtWidgets.QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Model", "Context", "$ / M in / out", "JSON"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSortingEnabled(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self._table.doubleClicked.connect(self._accept_selection)
        layout.addWidget(self._table, 1)

        toggles = QtWidgets.QHBoxLayout()
        self._free_only = QtWidgets.QCheckBox("Free models only")
        self._free_only.toggled.connect(self._apply_filter)
        self._json_only = QtWidgets.QCheckBox("Supports JSON output")
        self._json_only.setToolTip(
            "Structured mode asks the model for JSON. A model that cannot be "
            "asked for it will fail every generation, so this is ticked by "
            "default.")
        self._json_only.setChecked(True)
        self._json_only.toggled.connect(self._apply_filter)
        toggles.addWidget(self._free_only)
        toggles.addWidget(self._json_only)
        toggles.addStretch(1)
        self._count = QtWidgets.QLabel()
        self._count.setStyleSheet("color: gray;")
        toggles.addWidget(self._count)
        layout.addLayout(toggles)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._apply_filter()
        self._select(current)
        self._filter.setFocus()

    # ------------------------------------------------------------------ #
    def _visible(self) -> List:
        needle = self._filter.text()
        out = [m for m in self._models if m.matches(needle)]
        if self._free_only.isChecked():
            out = [m for m in out if m.free]
        if self._json_only.isChecked():
            out = [m for m in out if m.json_mode]
        return out

    def _apply_filter(self):
        rows = self._visible()
        # Sorting has to be off while filling, or Qt reorders rows mid-insert
        # and the id column stops lining up with its own row.
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for row, model in enumerate(rows):
            self._set(row, 0, model.id, tooltip=model.label)
            self._set(row, 1, _context_text(model.context), model.context)
            self._set(row, 2, _price_text(model), model.price_in)
            self._set(row, 3, "yes" if model.json_mode else "no")
        self._table.setSortingEnabled(True)
        self._count.setText(f"{len(rows)} of {len(self._models)}")

    def _set(self, row, column, text, sort_value=None, tooltip=""):
        item = QtWidgets.QTableWidgetItem(str(text))
        if sort_value is not None:
            # Sort numerically, not as "128k" < "1M" text.
            item.setData(QtCore.Qt.UserRole, sort_value)
            item.setData(QtCore.Qt.EditRole, sort_value)
            item.setText(str(text))
        if tooltip:
            item.setToolTip(tooltip)
        self._table.setItem(row, column, item)

    def _select(self, model_id: str):
        if not model_id:
            return
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is not None and item.text() == model_id:
                self._table.selectRow(row)
                self._table.scrollToItem(item)
                return

    def _accept_selection(self):
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 0)
        if item is None:
            return
        self.chosen = item.text()
        self.accept()


def choose_model(models: List, current: str = "", title: str = "Choose a model",
                 parent=None) -> Optional[str]:
    """Show the picker; return the chosen model id, or None if cancelled."""
    dialog = ModelPicker(models, current=current, title=title, parent=parent)
    if exec_dialog(dialog) == QtWidgets.QDialog.Accepted:
        return dialog.chosen
    return None
