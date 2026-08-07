"""Colours taken from the theme that is actually loaded, not from literals.

FreeCAD ships light and dark themes and repaints most of the UI through a
70,000-character stylesheet, so a colour that reads well in one theme can be
invisible in another. Measured under FreeCAD Dark: a text box is painted
``#191919`` with white text, which put the panel's old ``#2c3e50`` status text
at a contrast ratio of 1.4:1 - present in the widget, unreadable on the screen.

Two rules come out of that, and this module exists to make them cheap to follow:

* Ordinary text sets **no** colour at all, so it inherits whatever the theme
  chose. That is always right and never needs maintaining.
* Text that has to *mean* something - secondary, or wrong - derives its colour
  from the widget's own palette here.

Qt's palette does carry the stylesheet's colours, but only once the widget has
been polished, which is why every function below polishes first. Before a
widget is shown it inherits the application palette, which the theme has
already set, so these are safe to call early too.
"""

from __future__ import annotations

from .qt import QtGui

# Reds with real contrast on the backgrounds FreeCAD's themes actually use:
# 5.4:1 on white, 6.4:1 on FreeCAD Dark's #191919. One red cannot do both -
# anything legible on near-black is washed out on white and vice versa.
_DANGER_ON_LIGHT = "#c0392b"
_DANGER_ON_DARK = "#ff6b6b"

# How far a secondary colour is faded towards the background.
_MUTED_PERCENT = 60


def blend(first: QtGui.QColor, second: QtGui.QColor, percent: int) -> QtGui.QColor:
    """``percent`` of ``first`` mixed into ``second``."""
    keep = 100 - percent
    return QtGui.QColor(
        (first.red() * percent + second.red() * keep) // 100,
        (first.green() * percent + second.green() * keep) // 100,
        (first.blue() * percent + second.blue() * keep) // 100,
    )


def _palette(widget) -> QtGui.QPalette:
    widget.ensurePolished()
    return widget.palette()


def is_dark(widget) -> bool:
    """Is this widget sitting on a dark background?"""
    palette = _palette(widget)
    return palette.color(widget.backgroundRole()).lightness() < 128


def muted_color(widget) -> QtGui.QColor:
    """A secondary text colour for the theme in force.

    Qt's ``PlaceholderText`` role would be the obvious source, but under
    FreeCAD's stylesheets it comes back as plain white - identical to the
    primary text, so it says nothing. Fading the real text colour towards the
    real background is correct by construction in any theme.
    """
    palette = _palette(widget)
    return blend(palette.color(QtGui.QPalette.Text),
                 palette.color(QtGui.QPalette.Base), _MUTED_PERCENT)


def danger_color(widget) -> QtGui.QColor:
    """The colour for something that went wrong."""
    return QtGui.QColor(_DANGER_ON_DARK if is_dark(widget) else _DANGER_ON_LIGHT)


def muted(widget) -> str:
    """:func:`muted_color` as ``#rrggbb``, for a stylesheet or inline HTML."""
    return muted_color(widget).name()


def danger(widget) -> str:
    """:func:`danger_color` as ``#rrggbb``."""
    return danger_color(widget).name()
