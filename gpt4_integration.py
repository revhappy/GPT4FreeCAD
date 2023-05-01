import requests
import json
import os
from PySide2 import QtWidgets

def ask_for_api_key():
    api_key, ok = QtWidgets.QInputDialog.getText(None, "API Key", "Enter your OpenAI API key:")
    if ok and api_key:
        with open(os.path.join(os.path.expanduser("~"), "api_key.txt"), "w") as api_key_file:
            api_key_file.write(api_key)
        return api_key
    else:
        raise ValueError("API key not provided. Please enter a valid OpenAI API key.")

# Read API key from file or ask the user for it
api_key_path = os.path.join(os.path.expanduser("~"), "api_key.txt")
if os.path.exists(api_key_path):
    with open(api_key_path, "r") as api_key_file:
        API_KEY = api_key_file.read().strip()
else:
    API_KEY = ask_for_api_key()

API_ENDPOINT = "https://api.openai.com/v1/chat/completions"

def generate_chat_completion(messages, model="gpt-4", temperature=0.3, max_tokens=None):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }

    data = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    if max_tokens is not None:
        data["max_tokens"] = max_tokens

    response = requests.post(API_ENDPOINT, headers=headers, data=json.dumps(data))

    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"]
    else:
        raise Exception(f"Error {response.status_code}: {response.text}")

