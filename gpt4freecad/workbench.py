"""FreeCAD workbench + command registration (GUI only)."""

from __future__ import annotations

import os

import FreeCADGui as Gui

# Addon root (one level up from this package) holds the icons.
_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ICON = os.path.join(_ADDON_DIR, "gpticon.png")
_LOGO = os.path.join(_ADDON_DIR, "logo.svg")


class ShowPanelCommand:
    """Toolbar/menu command that opens the dockable panel."""

    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "GPT4FreeCAD panel",
            "ToolTip": "Open the GPT4FreeCAD panel to generate geometry from text",
        }

    def Activated(self):
        from .ui.panel import show_panel
        show_panel()

    def IsActive(self):
        return True


class SettingsCommand:
    """Toolbar/menu command that opens the settings dialog."""

    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "GPT4FreeCAD settings",
            "ToolTip": "Configure API keys and models",
        }

    def Activated(self):
        from .ui.settings import open_settings
        open_settings()

    def IsActive(self):
        return True


Gui.addCommand("GPT4FreeCAD_ShowPanel", ShowPanelCommand())
Gui.addCommand("GPT4FreeCAD_Settings", SettingsCommand())


class GPT4FreeCADWorkbench(Gui.Workbench):
    MenuText = "GPT4FreeCAD"
    ToolTip = "Generate parametric CAD geometry from natural language"
    Icon = _LOGO if os.path.exists(_LOGO) else _ICON

    _COMMANDS = ["GPT4FreeCAD_ShowPanel", "GPT4FreeCAD_Settings"]

    def Initialize(self):
        self.appendToolbar("GPT4FreeCAD", self._COMMANDS)
        self.appendMenu("GPT4FreeCAD", self._COMMANDS)

    def Activated(self):
        # Opening the workbench surfaces the panel automatically.
        from .ui.panel import show_panel
        show_panel()

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"
