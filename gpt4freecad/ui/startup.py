"""Workbench-independent entry points, installed at FreeCAD startup.

A workbench only announces itself once the user picks it from the workbench
selector: FreeCAD does not even call ``Initialize`` before that, so an addon that
lives purely inside a workbench is invisible until you go looking for it. This
module runs from ``InitGui.py`` (which FreeCAD *does* execute for every addon at
startup) and adds two entry points that do not depend on the active workbench:

* the dock panel, opened once the main window is up - dock widgets are owned by
  the main window, so it survives workbench switches, and
* a small toolbar, re-applied on every workbench activation because FreeCAD
  rebuilds the toolbar area each time the workbench changes.

Both are preferences (on by default) so a user who wants the plain FreeCAD
window back can have it.

The deferred start - poll ``mw.property("eventLoop")`` from a timer, then connect
to ``workbenchActivated`` - is the pattern FreeCAD's own Tux module uses; at
``InitGui`` time the main window exists but its event loop does not, and toolbars
added before the loop starts are discarded.
"""

from __future__ import annotations

import os

from .qt import QtCore, QtGui, QtWidgets, QAction
from ..config import get_config

_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ICON = os.path.join(_ADDON_DIR, "gpticon.png")

_TOOLBAR_TITLE = "GPT4FreeCAD"
_TOOLBAR_OBJECT = "GPT4FreeCADGlobalToolbar"
_WORKBENCH = "GPT4FreeCADWorkbench"
_GENERAL_PARAMS = "User parameter:BaseApp/Preferences/General"

# Command name, button text, tooltip.
_ACTIONS = [
    ("GPT4FreeCAD_ShowPanel", "GPT4FreeCAD",
     "Open the GPT4FreeCAD panel to generate geometry from text"),
    ("GPT4FreeCAD_Settings", "GPT4FreeCAD settings",
     "Configure API keys and models"),
]

_timer = None
_started = False


# --------------------------------------------------------------------------- #
# Entry point (called from InitGui.py)
# --------------------------------------------------------------------------- #
def install() -> None:
    """Arm the deferred startup. Safe to call more than once."""
    global _timer
    if _timer is not None or _started:
        return
    _timer = QtCore.QTimer()
    _timer.timeout.connect(_try_start)
    _timer.start(500)


def _try_start() -> None:
    """Run once the main window has an event loop; keep waiting until then."""
    global _started
    mw = _main_window()
    if mw is None or not mw.property("eventLoop"):
        return

    if _timer is not None:
        _timer.stop()
    _started = True

    try:
        mw.workbenchActivated.connect(_on_workbench_activated)
    except AttributeError:
        # Older FreeCAD without the signal: the toolbar still gets installed,
        # it just will not survive a workbench switch.
        pass

    apply_preferences(open_panel=True)


# --------------------------------------------------------------------------- #
# Toolbar
# --------------------------------------------------------------------------- #
def apply_preferences(open_panel: bool = False) -> None:
    """Show or hide the startup UI to match the current preferences.

    Called at startup and again after the settings dialog saves, so toggling a
    checkbox takes effect without restarting FreeCAD. ``open_panel`` is set at
    startup, and by the settings dialog when auto-show has just been switched
    on; otherwise the panel is left as the user last had it, since re-opening it
    on every save would fight someone who just closed it.
    """
    mw = _main_window()
    if mw is None:
        return

    cfg = get_config()
    if cfg.global_toolbar():
        _install_toolbar(mw)
    else:
        _remove_toolbar(mw)

    if open_panel and cfg.auto_show_panel():
        _show_panel()


def _install_toolbar(mw):
    toolbar = mw.findChild(QtWidgets.QToolBar, _TOOLBAR_OBJECT)
    if toolbar is None:
        toolbar = QtWidgets.QToolBar(_TOOLBAR_TITLE, mw)
        toolbar.setObjectName(_TOOLBAR_OBJECT)
        icon = QtGui.QIcon(_ICON) if os.path.exists(_ICON) else QtGui.QIcon()
        for command, text, tooltip in _ACTIONS:
            action = QAction(icon, text, toolbar)
            action.setToolTip(tooltip)
            action.setStatusTip(tooltip)
            action.triggered.connect(_runner(command))
            toolbar.addAction(action)
        mw.addToolBar(QtCore.Qt.TopToolBarArea, toolbar)
    toolbar.setVisible(True)
    return toolbar


def _remove_toolbar(mw):
    toolbar = mw.findChild(QtWidgets.QToolBar, _TOOLBAR_OBJECT)
    if toolbar is None:
        return
    mw.removeToolBar(toolbar)
    toolbar.setParent(None)
    toolbar.deleteLater()


def _on_workbench_activated(*_args) -> None:
    """Re-assert the toolbar after FreeCAD rebuilds the toolbar area."""
    mw = _main_window()
    if mw is None or not get_config().global_toolbar():
        return
    _install_toolbar(mw)
    # FreeCAD's own toolbar setup may run after this slot and hide us again, so
    # claim visibility once more at the end of the current event cycle.
    QtCore.QTimer.singleShot(0, lambda: _restore_visibility(mw))


def _restore_visibility(mw) -> None:
    toolbar = mw.findChild(QtWidgets.QToolBar, _TOOLBAR_OBJECT)
    if toolbar is not None and not toolbar.isVisible():
        toolbar.setVisible(True)


def _runner(command):
    def run(*_args):
        import FreeCADGui as Gui

        Gui.runCommand(command, 0)

    return run


# --------------------------------------------------------------------------- #
# Startup workbench
# --------------------------------------------------------------------------- #
def is_start_workbench() -> bool:
    """True when FreeCAD opens directly into the GPT4FreeCAD workbench."""
    params = _general_params()
    if params is None:
        return False
    return params.GetString("AutoloadModule", "") == _WORKBENCH


def set_start_workbench(enabled: bool) -> None:
    """Take over (or hand back) FreeCAD's startup workbench.

    The previous choice is remembered in our own preferences so unchecking the
    box restores it rather than guessing.
    """
    params = _general_params()
    if params is None:
        return
    cfg = get_config()
    current = params.GetString("AutoloadModule", "")

    if enabled:
        if current != _WORKBENCH:
            cfg.set_previous_start_workbench(current)
            params.SetString("AutoloadModule", _WORKBENCH)
    elif current == _WORKBENCH:
        # PartDesign is what FreeCAD itself falls back to.
        params.SetString(
            "AutoloadModule",
            cfg.previous_start_workbench() or "PartDesignWorkbench")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _main_window():
    try:
        import FreeCADGui as Gui

        return Gui.getMainWindow()
    except Exception:
        return None


def _general_params():
    try:
        import FreeCAD as App

        return App.ParamGet(_GENERAL_PARAMS)
    except Exception:
        return None


def _show_panel() -> None:
    try:
        from .panel import show_panel

        show_panel()
    except Exception as exc:
        try:
            import FreeCAD as App

            App.Console.PrintError(
                f"GPT4FreeCAD could not open its panel: {exc}\n")
        except Exception:
            pass
