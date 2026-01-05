# GPT-CAD

A FreeCAD plugin that integrates AI models to generate Python scripts for creating sketches and 3D models based on natural language input.

![Workbench Logo](logo.svg)

## Features

- **Multi-Platform AI Support** - Choose between multiple AI providers:
  - **OpenAI** - GPT-4o, GPT-4 Turbo, GPT-4, GPT-3.5 Turbo
  - **Anthropic Claude** - Claude 4, Claude 3.5 Sonnet, Claude 3 Opus
- **Interactive UI** - Provider and model selection dropdowns
- **Conversation History** - Multi-turn interactions for iterative design
- **Undo Support** - Easily revert generated operations
- **Persistent Settings** - Remembers your provider and model preferences

## Requirements

- FreeCAD 0.20 or later
- Python 3.x
- `requests` library
- API key for your chosen provider:
  - [OpenAI API Key](https://platform.openai.com/api-keys)
  - [Anthropic API Key](https://console.anthropic.com/)

## Installation

1. Open a terminal or command prompt with administrator privileges.

2. Clone this repository into the FreeCAD Mod folder:

   **Windows:**
   ```bash
   cd "C:\Program Files\FreeCAD 0.20\Mod"
   git clone https://github.com/revhappy/GPT4FreeCAD GPT-CAD
   ```

   **Linux:**
   ```bash
   cd ~/.FreeCAD/Mod
   git clone https://github.com/revhappy/GPT4FreeCAD GPT-CAD
   ```

   **macOS:**
   ```bash
   cd ~/Library/Preferences/FreeCAD/Mod
   git clone https://github.com/revhappy/GPT4FreeCAD GPT-CAD
   ```

3. Install the `requests` library:

   **Windows:**
   ```bash
   cd "C:\Program Files\FreeCAD 0.20\bin"
   python -m pip install requests
   ```

   **Linux/macOS:**
   ```bash
   pip install requests
   ```

## Usage

1. Launch FreeCAD.
2. Go to `Macro > Macros...`.
3. Navigate to the GPT-CAD folder in your Mod directory.
4. Select `GPTSTART.FCMacro` and click **Execute**.

### First Run

On first launch, you'll be prompted to enter your API key for the selected provider. The key is stored securely in your home directory (`~/.gptcad_keys.json`).

### Selecting a Provider

Use the **Provider** dropdown to switch between OpenAI and Claude. Each provider offers different models with varying capabilities:

| Provider | Recommended Model | Best For |
|----------|-------------------|----------|
| OpenAI | GPT-4o | Fast, capable, cost-effective |
| OpenAI | GPT-4 Turbo | Complex designs, longer context |
| Claude | Claude 4 | Detailed explanations, nuanced designs |
| Claude | Claude 3.5 Sonnet | Balance of speed and capability |

### Example Prompts

```
Create a cube with 10mm sides
```

```
Make a cylinder with radius 5mm and height 20mm, then cut a 3mm hole through the center
```

```
Design a simple bracket with mounting holes
```

### Adding to the Toolbar

1. In FreeCAD, go to `Tools > Customize...`.
2. Click on the **Macros** tab.
3. Follow the prompts to add the macro to your toolbar.

## Configuration

Settings are stored in your home directory:

| File | Purpose |
|------|---------|
| `~/.gptcad_config.json` | Provider, model, and preference settings |
| `~/.gptcad_keys.json` | API keys (permissions set to user-only) |

### Manual Configuration

You can edit `~/.gptcad_config.json` directly:

```json
{
  "provider": "openai",
  "model": "gpt-4o",
  "temperature": 0.3,
  "max_tokens": 4000
}
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Blank screen or no response | Enable Report View (`View > Panels > Report View`) to see errors |
| API errors | Check your API key and ensure you have credits/quota |
| Code execution fails | Try prompting "try again" or paste the error message |
| Rate limiting | Wait a moment and retry, or switch to a different model |

### Debug Mode

Enable the Report View in FreeCAD to see:
- Generated Python code
- Execution errors
- API response details

## Architecture

```
GPT-CAD/
├── ai_providers/           # AI provider implementations
│   ├── base.py             # Abstract provider interface
│   ├── openai_provider.py  # OpenAI GPT models
│   └── claude_provider.py  # Anthropic Claude models
├── config.py               # Configuration management
├── gpt.py                  # Main UI and logic
├── GPTSTART.FCMacro        # FreeCAD entry point
└── package.xml             # FreeCAD package metadata
```

## Adding New Providers

To add support for additional AI providers (e.g., Ollama, Azure OpenAI):

1. Create a new provider file in `ai_providers/`:

```python
from .base import AIProvider

class MyProvider(AIProvider):
    name = "myprovider"
    display_name = "My Provider"

    def generate_completion(self, messages, model=None, temperature=0.3, max_tokens=None):
        # Implementation here
        pass

    def get_available_models(self):
        return ["model-1", "model-2"]

    def get_default_model(self):
        return "model-1"

    def validate_api_key(self, api_key):
        # Validation logic
        return True
```

2. Register it in `ai_providers/__init__.py`:

```python
from .myprovider import MyProvider

PROVIDERS = {
    "openai": OpenAIProvider,
    "claude": ClaudeProvider,
    "myprovider": MyProvider,
}
```

## License

This project is licensed under the [MIT License](LICENSE).

Copyright (c) 2023 Robb Sharma

## Links

- [GitHub Repository](https://github.com/revhappy/GPT4FreeCAD)
- [OpenAI API Documentation](https://platform.openai.com/docs)
- [Anthropic Claude Documentation](https://docs.anthropic.com)
