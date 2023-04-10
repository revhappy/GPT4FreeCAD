import FreeCAD as App
import FreeCADGui as Gui
import Part
from FreeCAD import Base
from PySide2 import QtWidgets

from gpt4_integration import generate_chat_completion


def process_command(command):
    messages = [
        {
            "role": "system",
            "content": "You are a FreeCAD scripter. You will output the code for the shape the user inputs in the Python Console",
        },
        {"role": "user", "content": command},
    ]

    response_text = generate_chat_completion(messages, max_tokens=4000)
    return response_text


class GPTCommandDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(GPTCommandDialog, self).__init__(parent)
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("GPT Command Input")
        self.resize(400, 150)

        self.verticalLayout = QtWidgets.QVBoxLayout(self)

        self.label = QtWidgets.QLabel("Enter your command:")
        self.verticalLayout.addWidget(self.label)

        self.command_input = QtWidgets.QLineEdit()
        self.verticalLayout.addWidget(self.command_input)

        self.execute_button = QtWidgets.QPushButton("Execute")
        self.execute_button.clicked.connect(self.execute_command)
        self.verticalLayout.addWidget(self.execute_button)

    def execute_command(self):
        command = self.command_input.text()
        if not command:
            App.Console.PrintError("No command found.\n")
            return

        try:
            response_text = process_command(command)

            # Check if the response contains code
            if "```python" in response_text and "\n```" in response_text:
                # Split the response into description and code parts
                description, code = response_text.split("```python\n", 1)
                code, _ = code.split("\n```", 1)

                # Print the description in the Report View
                App.Console.PrintMessage(f"{description}\n")

                # Execute the generated code in the Python environment
                exec(code, {"App": App, "Part": Part, "Base": Base})
            else:
                # If there is no code, print the response in the Report View
                App.Console.PrintMessage(f"Response: {response_text}\n")
        except Exception as e:
            App.Console.PrintError(f"Error: {str(e)}\n")


def show_gpt_command_dialog():
    dialog = GPTCommandDialog(Gui.getMainWindow())
    dialog.exec_()
