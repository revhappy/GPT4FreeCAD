"""
GPT-CAD: AI-Powered FreeCAD Plugin

This plugin enables natural language interaction with FreeCAD,
allowing users to describe 3D parts and have AI generate the
corresponding Python code.

Supports multiple AI providers:
- OpenAI (GPT-4, GPT-4o, GPT-3.5)
- Anthropic Claude (Claude 3.5, Claude 3)

Usage:
    Run GPTSTART.FCMacro or import gpt module and call show_gpt_command_dialog()
"""

__version__ = "2.0.0"
__author__ = "GPT-CAD Contributors"

import gpt

gpt.show_gpt_command_dialog()
