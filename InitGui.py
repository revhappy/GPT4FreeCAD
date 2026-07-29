# GPT4FreeCAD - FreeCAD workbench bootstrap.
# FreeCAD executes this file automatically (GUI mode) for every addon in Mod/.
import os
import sys
import importlib

# Ensure this addon directory is importable as the `gpt4freecad` package even on
# setups that do not auto-add the Mod subfolder to sys.path.
try:
    _ADDON_DIR = os.path.normcase(os.path.abspath(os.path.dirname(__file__)))
except NameError:  # __file__ not defined in some embed contexts
    _ADDON_DIR = None

if _ADDON_DIR:
    # An older app-wide installation may have imported the same package name
    # first. Put this addon first and evict that stale package tree so normal
    # FreeCAD startup loads the files beside this InitGui.py.
    sys.path[:] = [entry for entry in sys.path
                   if os.path.normcase(os.path.abspath(entry or os.curdir)) != _ADDON_DIR]
    sys.path.insert(0, _ADDON_DIR)

    loaded = sys.modules.get("gpt4freecad")
    loaded_file = os.path.normcase(os.path.abspath(
        getattr(loaded, "__file__", "") or ""))
    if loaded is not None and not loaded_file.startswith(_ADDON_DIR + os.sep):
        for module_name in list(sys.modules):
            if module_name == "gpt4freecad" or module_name.startswith("gpt4freecad."):
                del sys.modules[module_name]
        importlib.invalidate_caches()

import FreeCAD  # noqa: F401  (ensures FreeCAD is initialised)
import FreeCADGui as Gui

try:
    from gpt4freecad.workbench import GPT4FreeCADWorkbench
    Gui.addWorkbench(GPT4FreeCADWorkbench())
except Exception as exc:  # surface load errors in the Report view instead of failing silently
    FreeCAD.Console.PrintError(f"GPT4FreeCAD failed to load: {exc}\n")
