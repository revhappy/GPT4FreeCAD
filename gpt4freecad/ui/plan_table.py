"""The plan, as a table you can read instead of JSON you have to parse.

Sits above the plan box in structured mode and follows whatever is in it - the
model's reply, a template, or a hand edit half-typed. It never validates and
never blocks anything: a plan that will not parse gets a note saying so, and a
plan that parses but would not build is still drawn, with the validator's
complaint underneath it.

The note is a label rather than a row of the table on purpose. A validator
message is a sentence, and a sentence in a cell either forces the first column
to the width of the whole message or gets elided down to nothing useful.

Every colour comes from :mod:`gpt4freecad.ui.theme`, which reads the palette the
active theme actually installed - see that module for why nothing here is a
literal.
"""

from __future__ import annotations

from .qt import QtCore, QtGui, QtWidgets
from . import theme

_HEADERS = ("#", "Op", "Name", "Details")
_ROW_HEIGHT = 18
_DETAILS = len(_HEADERS) - 1

# How far the striping is faded from the background. Enough to follow a row
# across the width of a narrow dock, not enough to read as a highlight.
_STRIPE_PERCENT = 7


class PlanTable(QtWidgets.QWidget):
    """Read-only view of an IR program. See :mod:`gpt4freecad.cad.describe`."""

    # Class defaults: Qt can deliver changeEvent from inside a constructor and
    # from inside ensurePolished, so theming has to be safe before (and
    # during) the instance's own set-up.
    _theming = True
    _muted = QtGui.QColor(QtCore.Qt.gray)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self.table = QtWidgets.QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(list(_HEADERS))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setCornerButtonEnabled(False)
        self.table.setHorizontalScrollMode(
            QtWidgets.QAbstractItemView.ScrollPerPixel)

        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        # Headers sit over left-aligned cells; centring them reads as a
        # mismatch once the Details column stretches.
        header.setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        for column in range(_DETAILS):
            header.setSectionResizeMode(
                column, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_DETAILS, QtWidgets.QHeaderView.Stretch)

        self.note = QtWidgets.QLabel()
        self.note.setWordWrap(True)
        self.note.setVisible(False)

        layout.addWidget(self.table, 1)
        layout.addWidget(self.note)

        # The panel is a narrow dock; it must be allowed to get narrower still.
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                           QtWidgets.QSizePolicy.Ignored)
        self.table.setMinimumSize(0, 0)

        self._theming = False
        self._apply_theme()

    # ------------------------------------------------------------------ #
    # Theme
    # ------------------------------------------------------------------ #
    def _apply_theme(self):
        """Derive the stripe and note colours from the palette in force."""
        if self._theming:
            return  # setPalette below re-enters through changeEvent
        self._theming = True
        try:
            self.table.ensurePolished()
            palette = self.table.palette()
            palette.setColor(
                QtGui.QPalette.AlternateBase,
                theme.blend(palette.color(QtGui.QPalette.Text),
                            palette.color(QtGui.QPalette.Base),
                            _STRIPE_PERCENT))
            self.table.setPalette(palette)
            self._muted = theme.muted_color(self.table)
            self.note.setStyleSheet(f"color: {self._muted.name()};")
        finally:
            self._theming = False

    def changeEvent(self, event):
        """Re-derive the colours when the theme changes under us."""
        if event.type() in (QtCore.QEvent.PaletteChange,
                            QtCore.QEvent.StyleChange):
            self._apply_theme()
        super().changeEvent(event)

    def showEvent(self, event):
        # Before the table is shown it only has the application palette; the
        # stylesheet's own colours arrive with the first polish in the window.
        self._apply_theme()
        super().showEvent(event)

    # ------------------------------------------------------------------ #
    # Content
    # ------------------------------------------------------------------ #
    def set_rows(self, rows, note: str = ""):
        """Show one line per operation, with an optional note underneath."""
        self.table.clearContents()
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            self._set_row(index, row)
        self._set_note(note)

    def set_message(self, text: str):
        """Empty the table and say why."""
        self.table.clearContents()
        self.table.setRowCount(0)
        self._set_note(text)

    # ------------------------------------------------------------------ #
    def _set_row(self, index, row):
        cells = (str(row.index), row.op, row.name, row.detail)
        for column, text in enumerate(cells):
            item = QtWidgets.QTableWidgetItem(text)
            item.setToolTip(row.source)
            if column == 0:
                item.setForeground(self._muted)
                item.setTextAlignment(QtCore.Qt.AlignRight |
                                      QtCore.Qt.AlignVCenter)
            # The end product is the object the user is actually getting; the
            # rest are scaffolding a later step consumes.
            if column == 2 and row.result:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.table.setItem(index, column, item)

    def _set_note(self, text):
        self.note.setText(text)
        self.note.setVisible(bool(text))
