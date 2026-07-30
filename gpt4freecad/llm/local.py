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
import os
import urllib.error
import urllib.parse
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
    # A .gguf to activate on demand. Empty means "attach to a server someone
    # else started" - the original behaviour.
    model_path = ""

    # ------------------------------------------------------------------ #
    def chat(self, request: ChatRequest, api_key: str = "") -> str:
        """Run one local chat turn. ``api_key`` is accepted but unused."""
        timeout = max(request.timeout, _MIN_TIMEOUT)
        schema = request.json_schema if request.json_mode else None
        # Activate first if needed. Generation already runs on a background
        # worker, so a 30-second model load cannot block FreeCAD's UI.
        self.activate()
        client = _sdk_client(self.base_url, timeout)

        if client is not None:
            return _chat_via_sdk(client, request, schema)
        return _chat_via_http(self.base_url, request, schema, timeout)

    # ------------------------------------------------------------------ #
    def activate(self) -> Optional[str]:
        """Ensure a server is running for ``model_path``; return a status line.

        Returns None when there was nothing to do (already serving, or no model
        configured, so the caller should just try to attach). Raises LLMError if
        a model is configured but cannot be activated - that is a real failure
        the user needs to see, not something to fall through on.
        """
        return activate_model(self.model_path, self.base_url)

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
# Activation: start the model ourselves rather than making the user do it
#
# The SDK's MachineServer supervises `machine serve` - it reuses a healthy server
# on the same port instead of loading a second copy of a multi-gigabyte model,
# restarts it if llama.cpp dies, and tears the whole process tree down at exit.
# We keep one per process: FreeCAD is long-lived, and reloading the panel must
# not strand a loaded model or pay the load cost twice.
# --------------------------------------------------------------------------- #
_SERVER = None  # machine_activation.MachineServer, once activated


def activate_model(model_path: str, base_url: str) -> Optional[str]:
    """Start a local server for ``model_path`` if one is not already serving."""
    global _SERVER

    if not model_path:
        return None  # Attach-only mode; the caller reports if nothing answers.
    if _reachable(base_url):
        return None  # Something is already serving here - reuse it.

    try:
        from machine_activation import LlamaServer
    except Exception as exc:  # noqa: BLE001
        raise LLMError(
            "A local model is configured, but the 'machine-activation' package "
            "is not installed in FreeCAD's Python, so GPT4FreeCAD cannot start "
            f"the model itself ({exc}).\n\n"
            "Install it (it has no dependencies of its own):\n"
            "    pip install machine-activation"
        ) from exc

    if not os.path.isfile(model_path):
        raise LLMError(f"Local model file not found:\n    {model_path}")

    try:
        # auto_fetch: on a machine with no inference backend yet, download one
        # into the per-user cache instead of sending the user to a terminal.
        # LlamaServer runs llama-server directly - no Node.js, no npm, no CLI.
        server = LlamaServer(
            model_path, port=_port_of(base_url), auto_fetch=True, on_log=_log_line)
        server.start()
    except Exception as exc:  # noqa: BLE001 - MachineError and friends
        raise LLMError(
            f"Could not start the local model:\n    {model_path}\n\n{exc}"
        ) from exc

    _SERVER = server
    return f"Loaded {os.path.basename(model_path)} — serving at {server.base_url}"


def _log_line(line: str) -> None:
    """Forward llama.cpp's startup output to FreeCAD's console if there is one."""
    try:
        import FreeCAD as App

        App.Console.PrintLog(f"GPT4FreeCAD (local model): {line}\n")
    except Exception:  # noqa: BLE001 - headless / no FreeCAD
        pass


def deactivate_model() -> bool:
    """Stop a server we started. True if one was running."""
    global _SERVER
    if _SERVER is None:
        return False
    try:
        _SERVER.stop()
    except Exception:  # noqa: BLE001 - shutting down should never raise upward
        pass
    _SERVER = None
    return True


def activated_model() -> Optional[str]:
    """The base URL of a server this process started, if any."""
    if _SERVER is None:
        return None
    try:
        return _SERVER.base_url
    except Exception:  # noqa: BLE001 - not running
        return None


def _reachable(base_url: str) -> bool:
    try:
        return bool(_get_json(f"{base_url.rstrip('/')}/health", 5).get("status") == "ok")
    except LLMError:
        return False


def _port_of(base_url: str) -> int:
    try:
        return int(urllib.parse.urlparse(base_url).port or 8177)
    except Exception:  # noqa: BLE001
        return 8177


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
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    if not schema:
        return _content_of(_post_json(url, payload, timeout))

    # Two spellings of the same request. `machine serve` compiles the
    # OpenAI-style json_schema form itself; a bare llama-server only understands
    # {"type": "json_object", "schema": ...} and answers the other form with an
    # empty message after burning the whole token budget. Try each.
    for response_format in (
        {"type": "json_schema", "json_schema": {"schema": schema}},
        {"type": "json_object", "schema": schema},
    ):
        content = _content_of(
            _post_json(url, dict(payload, response_format=response_format), timeout),
            required=False,
        )
        if content:
            return content
    raise LLMError(
        "The local model returned an empty reply for a schema-constrained "
        "request. The server accepted the schema but produced nothing — try a "
        "smaller/simpler request, or a stronger model."
    )


def _content_of(data: dict, required: bool = True) -> str:
    choices = data.get("choices") or []
    if not choices:
        if required:
            raise LLMError("The local model server returned no choices.")
        return ""
    content = (choices[0].get("message") or {}).get("content") or ""
    if not content and required:
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
