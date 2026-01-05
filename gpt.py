"""
GPT-CAD Main UI Module.
Provides the dialog interface for AI-powered FreeCAD scripting.
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part
from FreeCAD import Base
from PySide2 import QtWidgets, QtGui, QtCore

from ai_providers import get_provider, list_providers, PROVIDERS
from config import get_config, ask_for_api_key


SYSTEM_PROMPT = "You are a FreeCAD scripter. You will output and execute the Python code for the shape the user inputs"


def get_configured_provider():
    """Get the currently configured AI provider with API key set."""
    config = get_config()
    provider_name = config.provider

    provider = get_provider(provider_name)

    # Check for API key
    api_key = config.get_api_key(provider_name)
    if not api_key:
        api_key = ask_for_api_key(provider_name, provider.display_name)
        if not api_key:
            raise ValueError(f"API key not provided for {provider.display_name}")

    provider.api_key = api_key
    return provider


def process_command(command, conversation_history):
    """
    Process a user command and generate AI response.

    Args:
        command: User's natural language description
        conversation_history: List of previous messages

    Returns:
        AI-generated response text
    """
    config = get_config()
    provider = get_configured_provider()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": command})

    response_text = provider.generate_completion(
        messages,
        model=config.model,
        temperature=config.temperature,
        max_tokens=config.max_tokens
    )

    return response_text


def ensure_active_document():
    """Ensure there's an active FreeCAD document."""
    if not App.ActiveDocument:
        App.newDocument("Unnamed")


class GPTCommandDialog(QtWidgets.QDialog):
    """Main dialog for GPT-CAD interaction."""

    def __init__(self, parent=None):
        super(GPTCommandDialog, self).__init__(parent)
        self.setWindowModality(QtCore.Qt.NonModal)
        self.conversation_history = []
        self.init_ui()
        self.load_settings()

    def init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle("GPT-CAD - AI-Powered FreeCAD")
        self.resize(650, 500)
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint
            | QtCore.Qt.WindowMinimizeButtonHint
        )

        self.main_layout = QtWidgets.QVBoxLayout(self)

        # Provider/Model selection section
        self.settings_group = QtWidgets.QGroupBox("AI Settings")
        self.settings_layout = QtWidgets.QHBoxLayout(self.settings_group)

        # Provider dropdown
        self.provider_label = QtWidgets.QLabel("Provider:")
        self.provider_combo = QtWidgets.QComboBox()
        for provider_name in list_providers():
            provider_class = PROVIDERS[provider_name]
            self.provider_combo.addItem(provider_class.display_name, provider_name)
        self.provider_combo.currentIndexChanged.connect(self.on_provider_changed)

        # Model dropdown
        self.model_label = QtWidgets.QLabel("Model:")
        self.model_combo = QtWidgets.QComboBox()

        self.settings_layout.addWidget(self.provider_label)
        self.settings_layout.addWidget(self.provider_combo)
        self.settings_layout.addSpacing(20)
        self.settings_layout.addWidget(self.model_label)
        self.settings_layout.addWidget(self.model_combo)
        self.settings_layout.addStretch()

        self.main_layout.addWidget(self.settings_group)

        # Conversation history area
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setMinimumHeight(200)
        self.main_layout.addWidget(self.scroll_area)

        self.scroll_widget = QtWidgets.QWidget()
        self.scroll_area.setWidget(self.scroll_widget)
        self.scroll_layout = QtWidgets.QVBoxLayout(self.scroll_widget)
        self.scroll_layout.addStretch()

        # Input section
        self.label = QtWidgets.QLabel("Describe your part:")
        self.main_layout.addWidget(self.label)

        self.command_input = QtWidgets.QLineEdit()
        self.command_input.setPlaceholderText("e.g., Create a cube with 10mm sides")
        self.command_input.returnPressed.connect(self.execute_command)
        self.main_layout.addWidget(self.command_input)

        # Buttons
        self.button_layout = QtWidgets.QHBoxLayout()

        self.execute_button = QtWidgets.QPushButton("Execute")
        self.execute_button.clicked.connect(self.execute_command)
        self.execute_button.setDefault(True)
        self.button_layout.addWidget(self.execute_button)

        self.undo_button = QtWidgets.QPushButton("Undo")
        self.undo_button.clicked.connect(self.undo_last_command)
        self.button_layout.addWidget(self.undo_button)

        self.clear_button = QtWidgets.QPushButton("Clear History")
        self.clear_button.clicked.connect(self.clear_history)
        self.button_layout.addWidget(self.clear_button)

        self.main_layout.addLayout(self.button_layout)

    def load_settings(self):
        """Load saved settings into UI."""
        config = get_config()

        # Set provider
        provider_index = self.provider_combo.findData(config.provider)
        if provider_index >= 0:
            self.provider_combo.setCurrentIndex(provider_index)

        self.update_model_list()

        # Set model if specified
        if config.model:
            model_index = self.model_combo.findText(config.model)
            if model_index >= 0:
                self.model_combo.setCurrentIndex(model_index)

    def on_provider_changed(self, index):
        """Handle provider selection change."""
        provider_name = self.provider_combo.currentData()
        config = get_config()
        config.provider = provider_name
        config.model = None  # Reset model when provider changes
        self.update_model_list()

    def update_model_list(self):
        """Update model dropdown based on selected provider."""
        self.model_combo.clear()

        provider_name = self.provider_combo.currentData()
        if not provider_name:
            return

        provider = get_provider(provider_name)
        models = provider.get_available_models()
        default_model = provider.get_default_model()

        for model in models:
            display_name = model
            if model == default_model:
                display_name = f"{model} (default)"
            self.model_combo.addItem(display_name, model)

    def get_selected_model(self):
        """Get the currently selected model."""
        return self.model_combo.currentData()

    def add_message_to_display(self, role: str, content: str):
        """Add a message to the conversation display."""
        # Remove the stretch at the end
        stretch_item = self.scroll_layout.takeAt(self.scroll_layout.count() - 1)

        # Create message widget
        if role == "user":
            label = QtWidgets.QLabel(f"<b>You:</b> {content}")
            label.setStyleSheet("background-color: #e3f2fd; padding: 8px; border-radius: 4px;")
        else:
            # Truncate long responses for display
            display_content = content[:500] + "..." if len(content) > 500 else content
            label = QtWidgets.QLabel(f"<b>AI:</b><br>{display_content}")
            label.setStyleSheet("background-color: #f5f5f5; padding: 8px; border-radius: 4px;")

        label.setWordWrap(True)
        label.setTextFormat(QtCore.Qt.RichText)
        self.scroll_layout.addWidget(label)

        # Re-add stretch
        self.scroll_layout.addStretch()

        # Scroll to bottom
        QtCore.QTimer.singleShot(100, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        """Scroll conversation to bottom."""
        scrollbar = self.scroll_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def execute_command(self):
        """Execute the user's command."""
        command = self.command_input.text().strip()
        if not command:
            App.Console.PrintError("No command entered.\n")
            return

        # Update config with selected model
        config = get_config()
        config.model = self.get_selected_model()

        # Disable UI during processing
        self.execute_button.setEnabled(False)
        self.command_input.setEnabled(False)
        self.execute_button.setText("Processing...")

        try:
            # Add user message to display
            self.add_message_to_display("user", command)

            # Process command
            response_text = process_command(command, self.conversation_history)

            # Update conversation history with both user and assistant messages
            self.conversation_history.append({"role": "user", "content": command})
            self.conversation_history.append({"role": "assistant", "content": response_text})

            # Add AI response to display
            self.add_message_to_display("assistant", response_text)

            self.command_input.clear()

            ensure_active_document()

            # Extract and execute Python code
            if "```python" in response_text and "```" in response_text:
                # Extract code from markdown code block
                parts = response_text.split("```python", 1)
                if len(parts) > 1:
                    code_part = parts[1]
                    code = code_part.split("```", 1)[0].strip()

                    # Log the code
                    App.Console.PrintMessage(f"Executing generated code:\n{code}\n")

                    # Execute the generated code
                    exec(code, {"App": App, "Part": Part, "Base": Base})
            else:
                App.Console.PrintMessage(f"AI Response (no code detected):\n{response_text}\n")

        except Exception as e:
            App.Console.PrintError(f"Error: {str(e)}\n")
            # Show error in UI
            error_label = QtWidgets.QLabel(f"<b style='color: red;'>Error:</b> {str(e)}")
            error_label.setWordWrap(True)
            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, error_label)

        finally:
            # Re-enable UI
            self.execute_button.setEnabled(True)
            self.command_input.setEnabled(True)
            self.execute_button.setText("Execute")
            self.command_input.setFocus()

    def undo_last_command(self):
        """Undo the last FreeCAD operation."""
        if App.ActiveDocument is not None:
            if App.ActiveDocument.UndoCount > 0:
                App.ActiveDocument.undo()
                App.Console.PrintMessage("Undid the last command.\n")
            else:
                App.Console.PrintError("No actions to undo.\n")
        else:
            App.Console.PrintError("No active document.\n")

    def clear_history(self):
        """Clear conversation history."""
        self.conversation_history.clear()

        # Clear display
        while self.scroll_layout.count() > 1:
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        App.Console.PrintMessage("Conversation history cleared.\n")


def show_gpt_command_dialog():
    """Show the GPT-CAD dialog."""
    dialog = GPTCommandDialog(Gui.getMainWindow())
    dialog.show()


# Delayed initialization for FreeCAD startup
def delayed_show_dialog():
    """Show dialog with slight delay for FreeCAD initialization."""
    dialog = GPTCommandDialog(Gui.getMainWindow())
    dialog.show()


timer = QtCore.QTimer()
timer.setSingleShot(True)
timer.timeout.connect(delayed_show_dialog)
timer.start(0)
