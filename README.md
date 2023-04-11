# GPT-4-FreeCAD

# GPT-4-FreeCAD

This repository contains a FreeCAD Add-On that integrates OpenAI's GPT model to generate Python scripts for creating 3D shapes based on user input.

## Installation

1. Open a terminal or command prompt with administrator privileges.
2. Clone this repository directly into the FreeCAD Mod folder:

git clone https://github.com/revhappy/GPT4FreeCAD.git "C:\Program Files\FreeCAD 0.20\Mod\GPT4FreeCAD"


3. Add your OpenAI API key to the `gpt4intergration.py` file.
4. Open a command prompt and navigate to the FreeCAD bin folder:

cd "C:\Program Files\FreeCAD 0.20\bin"


5. Install the `requests` library:

python -m pip install requests


## Usage

1. Launch FreeCAD.
2. Go to `Macro > Macros...`.
3. Navigate to the folder: `C:\Program Files\FreeCAD 0.20\Mod\GPT4FreeCAD`.
4. Click on `GPTSTART.FCMacro` and click "Execute" to run the macro.

### Adding to the Toolbar

1. In FreeCAD, go to `Tools > Customize...`.
2. Click on the "Macros" tab.
3. Follow the directions to add the macro to your toolbar.

## License

This project is licensed under the [MIT License](LICENSE).

## Links

- [GitHub Repository](https://github.com/revhappy/GPT4FreeCAD)
