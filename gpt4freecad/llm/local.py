"""Machine Activation SDK adapter - local GGUF models, no cloud, no API key.

Talks to a ``machine serve`` process (the Machine Activation SDK's local
inference server, llama.cpp under the hood). Two transports, same wire format:

* If the ``machine_activation`` Python client is importable we use it, which
  also gives us the *activation report* - whether the model actually fits this
  machine, what acceleration it got, and what is degraded. No cloud API has an
  equivalent, and it is the difference between "the AI is slow" and "you are on
  CPU with a 7B model".
* Otherwise we speak the same HTTP directly with the standard library, so the
  provider works in FreeCAD's bundled Python without ``pip install``.

Either way JSON mode is **grammar-constrained server-side**: the schema is
compiled to GBNF and enforced inside llama.cpp's sampler, so a small local model
*cannot* emit a malformed CAD program. Asking a 4B model nicely for JSON fails
constantly; this cannot.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from .base import ChatRequest, LLMError, Provider, register

DEFAULT_BASE_URL = "http://127.0.0.1:8177"

# Local generation is far slower than a cloud call (CPU decode can be ~12 tok/s),
# so never let a short cloud-sized timeout kill a working build.
_MIN_TIMEOUT = 600


@register
class MachineProvider(Provider):
    id = "machine"
    label = "Local (Machine Activation)"
    api_key_url = "https://github.com/revhappy/MachineActivationSDK"
    # The server serves whichever model was loaded, so there is no fixed list;
    # Settings can discover the loaded id via `list_models`.
    default_models = []
    requires_key = False
    # Overridden from config at runtime, like OpenAIProvider.endpoint.
    base_url = DEFAULT_BASE_URL

    # ------------------------------------------------------------------ #
    def chat(self, request: ChatRequest, api_key: str = "") -> str:
        """Run one local chat turn. ``api_key`` is accepted but unused."""
        timeout = max(request.timeout, _MIN_TIMEOUT)
        schema = request.json_schema if request.json_mode else None
        client = _sdk_client(self.base_url, timeout)

        if client is not None:
            return _chat_via_sdk(client, request, schema)
        return _chat_via_http(self.base_url, request, schema, timeout)

    # ------------------------------------------------------------------ #
    def list_models(self) -> "list[str]":
        """Model ids the running server reports. Empty if it is not reachable."""
        try:
            data = _get_json(f"{self.base_url.rstrip('/')}/v1/models", 10)
        except LLMError:
            return []
        return [entry["id"] for entry in data.get("data", []) if entry.get("id")]

    def activation_summary(self) -> str:
        """One line on whether this machine can actually run the loaded model.

        Falls back to a plain reachability statement when the SDK client is not
        installed (the report is an SDK concept, not raw HTTP).
        """
        client = _sdk_client(self.base_url, 30)
        if client is not None:
            if not client.is_ready():
                # By far the most common state: nothing started yet. Say how to
                # fix it rather than reporting a failed report fetch.
                return _not_running_hint(self.base_url, "no server responding")
            try:
                report = client.activation()
            except Exception as exc:  # noqa: BLE001 - report, never raise
                return f"Could not read the activation report: {exc}"
            lines = [report.summary()]
            lines += [f"! {w}" for w in report.warnings]
            if not report.usable:
                lines += [f"! {r}" for r in report.reasons]
            return "\n".join(lines)

        models = self.list_models()
        if models:
            return (f"Server reachable at {self.base_url}; loaded: {', '.join(models)}.\n"
                    "Install the 'machine-activation' package for a full "
                    "activation report (fit, acceleration, warnings).")
        return f"No local model server reachable at {self.base_url}."


# --------------------------------------------------------------------------- #
# Transport: SDK client when available
# --------------------------------------------------------------------------- #
def _sdk_client(base_url: str, timeout: float):
    """A ``machine_activation.MachineClient``, or None if not installed."""
    try:
        from machine_activation import MachineClient
    except Exception:  # noqa: BLE001 - not installed / broken install
        return None
    try:
        return MachineClient(base_url=base_url, timeout=float(timeout))
    except Exception:  # noqa: BLE001
        return None


def _chat_via_sdk(client, request: ChatRequest, schema: Optional[dict]) -> str:
    """Generate through the SDK client, mapping its errors onto LLMError."""
    messages = list(request.messages)
    try:
        if schema:
            data = client.chat_json(
                messages, schema,
                max_tokens=request.max_tokens, temperature=request.temperature,
            )
            # The engine parses text, and a constrained dict is already valid.
            return json.dumps(data)
        return client.chat(
            messages,
            max_tokens=request.max_tokens, temperature=request.temperature,
        )
    except Exception as exc:  # noqa: BLE001 - MachineError/ModelNotReady/etc.
        raise LLMError(_not_running_hint(client.base_url, exc)) from exc


# --------------------------------------------------------------------------- #
# Transport: plain stdlib HTTP fallback
# --------------------------------------------------------------------------- #
def _chat_via_http(base_url: str, request: ChatRequest,
                   schema: Optional[dict], timeout: int) -> str:
    payload = {
        "messages": list(request.messages),
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "stream": False,
    }
    if request.model:
        payload["model"] = request.model
    if schema:
        # Same field the SDK posts; the server compiles it to a GBNF grammar.
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"schema": schema},
        }

    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    data = _post_json(url, payload, timeout)
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("The local model server returned no choices.")
    content = (choices[0].get("message") or {}).get("content")
    if not content:
        finish = choices[0].get("finish_reason")
        raise LLMError(
            f"The local model returned an empty message (finish_reason={finish})."
        )
    return content


def _opener():
    """A urllib opener that ignores the system proxy.

    Without this, ``http://127.0.0.1`` is routed through ``$http_proxy`` on a
    corporate machine: the proxy has no route back to this box, and anything
    that did get through would ship the user's prompts somewhere they never
    chose. Local traffic stays local, regardless of ``no_proxy``.
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _send(request, timeout: int) -> dict:
    try:
        with _opener().open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:600]
        except Exception:  # noqa: BLE001
            detail = str(exc)
        raise LLMError(f"Local model server HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(_not_running_hint(request.full_url, exc)) from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"Local model server sent invalid JSON: {exc}") from exc


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    return _send(request, timeout)


def _get_json(url: str, timeout: int) -> dict:
    return _send(urllib.request.Request(url, method="GET"), timeout)


def _not_running_hint(where: str, exc: Exception) -> str:
    """Actionable message for the most common local failure: nothing running."""
    return (
        f"Could not reach a local model server at {where}: {exc}\n\n"
        "Start one, then try again:\n"
        "    machine serve <path-to-model.gguf>\n\n"
        "The 'machine' CLI comes from the Machine Activation SDK "
        "(npm i machineai-activation). Check the address in Settings if you "
        "run the server on a different port."
    )
