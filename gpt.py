import FreeCAD as App
import FreeCADGui as Gui
import Part
from FreeCAD import Base
from PySide2 import QtWidgets, QtGui, QtCore

from gpt4_integration import generate_chat_completion


def process_command(command, conversation_history):
    messages = [{"role": "system", "content": "You are a FreeCAD scripter. You will output and execute the Python code for the shape the user inputs"}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": command})

    response_text = generate_chat_completion(messages, max_tokens=4000)
    return response_text

class GPTCommandDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(GPTCommandDialog, self).__init__(parent)
        self.setWindowModality(QtCore.Qt.NonModal)  # Set the dialog to be non-modal
        self.init_ui()
        self.conversation_history = []

    def init_ui(self):
        self.setWindowTitle("GPT4FreeCAD Input")
        self.resize(600, 400)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint | QtCore.Qt.WindowMinimizeButtonHint)  # Remove question mark and add minimize button

        self.verticalLayout = QtWidgets.QVBoxLayout(self)

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.verticalLayout.addWidget(self.scroll_area)

        self.scroll_widget = QtWidgets.QWidget()
        self.scroll_area.setWidget(self.scroll_widget)
        self.scroll_layout = QtWidgets.QVBoxLayout(self.scroll_widget)

        self.label = QtWidgets.QLabel("Describe your part:")
        self.verticalLayout.addWidget(self.label)

        self.command_input = QtWidgets.QLineEdit()
        self.verticalLayout.addWidget(self.command_input)

        self.execute_button = QtWidgets.QPushButton("Execute")
        self.execute_button.clicked.connect(self.execute_command)
        self.verticalLayout.addWidget(self.execute_button)

        self.undo_button = QtWidgets.QPushButton("Undo")
        self.undo_button.clicked.connect(self.undo_last_command)
        self.verticalLayout.addWidget(self.undo_button)

    def execute_command(self):
        command = self.command_input.text()
        if not command:
            App.Console.PrintError("No command found.\n")
            return

        try:
            response_text = process_command(command, self.conversation_history)
            self.conversation_history.append({"role": "user", "content": command})

            # Display user input in the scrollable area
            user_label = QtWidgets.QLabel(f"Input: {command}")
            user_label.setFont(QtGui.QFont("Arial", 9, QtGui.QFont.Bold))
            self.scroll_layout.addWidget(user_label)

            # Scroll to the bottom of the scrollable area
            self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())

            self.command_input.clear()

            # Check if the response contains code
            if "```python" in response_text and "\n```" in response_text:
                # Split the response into description and code parts
                _, code = response_text.split("```python\n", 1)
                code, _ = code.split("\n```", 1)

                # Print the code in the console
                App.Console.PrintMessage(f"{code}\n")

                # Execute the generated code in the Python environment
                exec(code, {"App": App, "Part": Part, "Base": Base})
        except Exception as e:
            App.Console.PrintError(f"Error: {str(e)}\n")

    def undo_last_command(self):
        if App.ActiveDocument is not None:
            App.ActiveDocument.undo()

def show_gpt_command_dialog():
    dialog = GPTCommandDialog(Gui.getMainWindow())
    dialog.show()

from PySide2.QtCore import QTimer

timer = QTimer()
timer.setSingleShot(True)
timer.timeout.connect(show_gpt_command_dialog)
timer.start(0)
