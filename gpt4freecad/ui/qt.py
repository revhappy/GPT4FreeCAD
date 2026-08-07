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


class _WheelGuard(QtCore.QObject):
    """Stops an unfocused combo box or spin box swallowing the scroll wheel.

    Qt's default is that any wheel event over a QComboBox changes its value,
    whether or not it has focus. In a dialog that is merely surprising. In a
    tall scrolling panel it is destructive: scrolling past the provider and
    model dropdowns silently switches provider or model, and the page does not
    move because the combo ate the event.

    So a wheel event only reaches these widgets when they actually have focus -
    meaning the user clicked into one first and is deliberately spinning
    through it. Otherwise the event is refused and Qt hands it to the scroll
    area behind, which is what the user was aiming at.
    """

    def eventFilter(self, widget, event):
        if event.type() == QtCore.QEvent.Wheel and not widget.hasFocus():
            event.ignore()
            return True
        return False


_wheel_guard = _WheelGuard()


def no_wheel(widget):
    """Exempt one widget from wheel-scrolling. Returns it, so it can wrap a
    constructor call inline."""
    widget.installEventFilter(_wheel_guard)
    # Wheel-scrolling must not be able to focus it either, or the guard would
    # let the second notch of the same scroll through.
    if widget.focusPolicy() == QtCore.Qt.WheelFocus:
        widget.setFocusPolicy(QtCore.Qt.StrongFocus)
    return widget


def guard_wheel(root):
    """Apply :func:`no_wheel` to every value-carrying widget under ``root``.

    Applied to a whole window rather than per widget, so a control added later
    cannot quietly reintroduce the problem.
    """
    # QTabBar is here for the same reason as the rest: Qt switches tab on a
    # wheel event over the bar, so scrolling past the Activity/Plan/Thinking
    # row would change which tab you are looking at.
    kinds = (QtWidgets.QComboBox, QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox,
             QtWidgets.QSlider, QtWidgets.QAbstractSpinBox, QtWidgets.QTabBar)
    for widget in root.findChildren(QtWidgets.QWidget):
        if isinstance(widget, kinds):
            no_wheel(widget)
    return root


__all__ = ["QtCore", "QtGui", "QtWidgets", "QAction", "exec_dialog",
           "no_wheel", "guard_wheel", "_BINDING"]
