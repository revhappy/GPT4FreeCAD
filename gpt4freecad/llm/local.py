"""Machine Activation SDK adapter - local GGUF models, no cloud, no API key.

Talks to a local inference server (llama.cpp under the hood) - either a
``machine serve`` process the user started, or a bare ``llama-server`` this
addon launches itself via :mod:`.backend`. Chat always goes over plain stdlib
HTTP so the provider works in FreeCAD's bundled Python without ``pip install``;
the SDK's ``machine_activation`` client, when importable, is used for one thing
only: the *activation report* - whether the model actually fits this machine,
what acceleration it got, and what is degraded. No cloud API has an equivalent,
and it is the difference between "the AI is slow" and "you are on CPU with a
7B model".

JSON mode is **grammar-constrained**, on every server. We compile the CAD schema
to GBNF ourselves (:mod:`.gbnf`) and send a ready grammar, which llama.cpp
enforces inside its sampler - so a small local model *cannot* emit a malformed
CAD program, whether it is behind ``machine serve`` or a bare ``llama-server``
this addon started. Asking a 4B model nicely for JSON fails constantly; this
cannot.
"""

from __future__ import annotations

import json
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
        # Activate first if needed. Generation already runs on a background
        # worker, so a 40-second model load cannot block FreeCAD's UI.
        self.activate()

        schema = request.json_schema if request.json_mode else None
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
# backend.py supervises a directly-spawned llama-server - it reuses a healthy
# server on the same port instead of loading a second copy of a multi-gigabyte
# model, and stops the process at interpreter exit. We keep one per process:
# FreeCAD is long-lived, and reloading the panel must not strand a loaded model
# or pay the load cost twice.
# --------------------------------------------------------------------------- #
_SERVER = None  # the `backend` module once it has started a server, else None


def activate_model(model_path: str, base_url: str) -> Optional[str]:
    """Start a local server for ``model_path`` if one is not already serving."""
    global _SERVER

    if not model_path:
        return None  # Attach-only mode; the caller reports if nothing answers.
    if _reachable(base_url):
        return None  # Something is already serving here - reuse it.

    # Standard library only, on purpose: FreeCAD embeds its own Python, and
    # telling a user to `pip install` into it is not a setup step most people
    # can even carry out. See backend.py.
    from . import backend

    global _ACTIVE_URL
    try:
        status = backend.start(model_path, base_url, port=_port_of(base_url),
                               on_log=_log_line)
    except backend.BackendError as exc:
        raise LLMError(str(exc)) from exc

    _SERVER = backend
    _ACTIVE_URL = base_url
    return status


def _log_line(line: str) -> None:
    """Forward llama.cpp's startup output to FreeCAD's console if there is one."""
    try:
        import FreeCAD as App

        App.Console.PrintLog(f"GPT4FreeCAD (local model): {line}\n")
    except Exception:  # noqa: BLE001 - headless / no FreeCAD
        pass


def deactivate_model() -> bool:
    """Stop a model we started. True if one was running."""
    global _SERVER
    module, _SERVER = _SERVER, None
    if module is None:
        return False
    try:
        return bool(module.stop())
    except Exception:  # noqa: BLE001 - shutting down should never raise upward
        return False


def activated_model() -> Optional[str]:
    """The base URL of a model this process started, if any."""
    from . import backend

    return _ACTIVE_URL if backend.started_here() else None


def _reachable(base_url: str) -> bool:
    try:
        return bool(_get_json(f"{base_url.rstrip('/')}/health", 5).get("status") == "ok")
    except LLMError:
        return False


_ACTIVE_URL: Optional[str] = None


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


# --------------------------------------------------------------------------- #
# Transport: plain stdlib HTTP - the only path, so FreeCAD's bundled Python
# needs nothing installed. The SDK client is still used for the activation
# report, which is a thing only `machine serve` can answer.
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

    # Compile the schema ourselves and hand the server a ready grammar. Every
    # llama.cpp server takes `grammar` and enforces it in the sampler, so this
    # works the same on a bare llama-server we started as on `machine serve` -
    # and it is what makes a small local model unable to emit a malformed CAD
    # program. Compiling is a few milliseconds and the result is cached.
    content = _content_of(
        _post_json(url, dict(payload, grammar=_grammar_for(schema)), timeout),
        required=False,
    )
    if content:
        return content

    # A server that ignored `grammar` gave us nothing to work with. Fall back to
    # the two response_format spellings - servers disagree on which they take,
    # and some answer the wrong one with an empty message rather than an error.
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
        "request. The server accepted the grammar but produced nothing — try a "
        "smaller/simpler request, or a stronger model."
    )


# Compiling is cheap (~6 ms for the full CAD schema) but the schema is identical
# on every request, so do it once per process.
_GRAMMAR_CACHE: "dict[str, str]" = {}


def _grammar_for(schema: dict) -> str:
    key = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    grammar = _GRAMMAR_CACHE.get(key)
    if grammar is None:
        from .gbnf import json_schema_to_gbnf

        grammar = json_schema_to_gbnf(schema)
        _GRAMMAR_CACHE[key] = grammar
    return grammar


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
