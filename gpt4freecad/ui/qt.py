"""Qt binding shim.

FreeCAD 0.20/0.21 ship PySide2 (Qt5); FreeCAD 1.0+ ships PySide6 (Qt6). This
module exposes ``QtCore``, ``QtGui``, ``QtWidgets`` and a couple of helpers so the
rest of the UI does not care which is present. The notable Qt5->Qt6 difference we
hit is that ``QAction`` moved from ``QtWidgets`` to ``QtGui``.
"""

from __future__ import annotations

try:  # PySide2 (Qt5) - FreeCAD 0.20/0.21
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
    _BINDING = "PySide2"
except ImportError:  # PySide6 (Qt6) - FreeCAD 1.0+
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
    _BINDING = "PySide6"


# QAction lives in QtWidgets on Qt5, QtGui on Qt6.
QAction = getattr(QtWidgets, "QAction", None) or QtGui.QAction


def exec_dialog(dialog):
    """Modally execute a dialog regardless of binding (.exec vs .exec_)."""
    runner = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
    return runner()


__all__ = ["QtCore", "QtGui", "QtWidgets", "QAction", "exec_dialog", "_BINDING"]
