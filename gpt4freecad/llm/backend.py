"""Run a local model with nothing but the Python standard library.

GPT4FreeCAD ships as a FreeCAD addon, and FreeCAD embeds its own Python. Anything
that says "pip install X" is a dead end there: the user has no obvious shell for
that interpreter, and on Windows it may not even have pip. So obtaining and
running a local inference backend has to work with what FreeCAD already has -
``urllib``, ``zipfile``, ``subprocess`` - and nothing else.

That is what this module does:

* :func:`find_server` locates a ``llama-server`` binary.
* :func:`fetch_server` downloads one for this machine, once, into a per-user
  cache shared with the Machine Activation SDK (``~/.machine/llama-cpp``), so a
  binary fetched by either tool is found by both.
* :func:`start` / :func:`stop` supervise it, reusing a server that is already
  listening rather than loading a second copy of a multi-gigabyte model.

The Machine Activation SDK's Python client does all of this more thoroughly, and
we use it when it happens to be importable. This exists so that not having it is
not a wall.
"""

from __future__ import annotations

import atexit
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from typing import Callable, Optional

_REPO = "ggml-org/llama.cpp"
_UA = "gpt4freecad"

# Same per-host asset choices and vendor slugs the SDK uses, deliberately: a
# binary fetched by `machine fetch-runtime` must satisfy us too, and vice versa.
_TARGETS = {
    ("win32", "amd64"): ("win-x64", r"^llama-b(\d+)-bin-win-cpu-x64\.zip$"),
    ("win32", "x86_64"): ("win-x64", r"^llama-b(\d+)-bin-win-cpu-x64\.zip$"),
    ("darwin", "arm64"): ("macos-arm64", r"^llama-b(\d+)-bin-macos-arm64\.zip$"),
    ("darwin", "x86_64"): ("macos-x64", r"^llama-b(\d+)-bin-macos-x64\.zip$"),
    ("linux", "x86_64"): ("linux-x64", r"^llama-b(\d+)-bin-ubuntu-x64\.zip$"),
}


class BackendError(Exception):
    """The local inference backend could not be obtained or started."""


def _exe() -> str:
    return "llama-server.exe" if sys.platform == "win32" else "llama-server"


def _target():
    key = (sys.platform, platform.machine().lower())
    target = _TARGETS.get(key)
    if target is None:
        raise BackendError(
            f"No prebuilt llama-server is published for {key[0]}/{key[1]}.\n"
            "Build one yourself and set MACHINE_LLAMA_SERVER to its path."
        )
    return target


def cache_dir() -> str:
    """Per-user home for the backend, shared with the Machine Activation SDK."""
    home = os.environ.get("MACHINE_HOME")
    base = home or os.path.join(os.path.expanduser("~"), ".machine")
    return os.path.join(base, "llama-cpp")


def find_server() -> Optional[str]:
    """An existing llama-server: explicit env var, the shared cache, then PATH."""
    explicit = os.environ.get("MACHINE_LLAMA_SERVER")
    if explicit and os.path.isfile(explicit):
        return explicit
    for slug, _pattern in _TARGETS.values():
        candidate = os.path.join(cache_dir(), slug, _exe())
        if os.path.isfile(candidate):
            return candidate
    return shutil.which(_exe())


def fetch_server(on_log: Optional[Callable[[str], None]] = None) -> str:
    """Download llama-server into the shared cache. Returns the binary path."""
    log = on_log or (lambda _line: None)
    slug, pattern = _target()
    destination = os.path.join(cache_dir(), slug)
    binary = os.path.join(destination, _exe())

    release = _get_json(f"https://api.github.com/repos/{_REPO}/releases/latest")
    matcher = re.compile(os.environ.get("LLAMA_CPP_ASSET") or pattern)
    asset = next((a for a in release.get("assets", [])
                  if matcher.search(a.get("name", ""))), None)
    if asset is None:
        raise BackendError(
            f"No llama.cpp download matched this machine in release "
            f"{release.get('tag_name')}."
        )

    os.makedirs(destination, exist_ok=True)
    archive = os.path.join(destination, "_download.zip")
    size_mb = (asset.get("size") or 0) / 1e6
    log(f"Downloading inference backend ({size_mb:.0f} MB, one time)…")
    _download(asset["browser_download_url"], archive)

    log("Extracting…")
    try:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(destination)
    except zipfile.BadZipFile as exc:
        raise BackendError(f"The downloaded backend archive is corrupt: {exc}") from exc
    finally:
        try:
            os.remove(archive)
        except OSError:
            pass

    _flatten(destination)
    if not os.path.isfile(binary):
        raise BackendError(f"Backend downloaded but {_exe()} was not found in {destination}.")
    if sys.platform != "win32":
        for name in os.listdir(destination):
            path = os.path.join(destination, name)
            if os.path.isfile(path):
                os.chmod(path, 0o755)

    build = re.search(r"-b(\d+)-", asset["name"])
    with open(os.path.join(destination, "version.json"), "w", encoding="utf-8") as handle:
        json.dump({"build": f"b{build.group(1)}" if build else release.get("tag_name"),
                   "asset": asset["name"], "platform": slug, "exe": _exe()}, handle, indent=2)
    log(f"Backend ready: {binary}")
    return binary


def _flatten(directory: str) -> None:
    """llama.cpp archives sometimes nest everything under build/bin/."""
    exe = _exe()
    if os.path.isfile(os.path.join(directory, exe)):
        return
    for root, _dirs, files in os.walk(directory):
        if exe in files and os.path.abspath(root) != os.path.abspath(directory):
            for name in os.listdir(root):
                shutil.move(os.path.join(root, name), os.path.join(directory, name))
            return


def _get_json(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/vnd.github+json"})
    try:
        with _opener().open(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise BackendError(
            f"Could not reach GitHub to download the inference backend: {exc}\n\n"
            "Check your connection, or set MACHINE_LLAMA_SERVER to a llama-server "
            "binary you already have."
        ) from exc


def _download(url: str, destination: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with _opener().open(request, timeout=900) as response, \
                open(destination, "wb") as handle:
            shutil.copyfileobj(response, handle)
    except urllib.error.URLError as exc:
        raise BackendError(f"Downloading the inference backend failed: {exc}") from exc


def _opener():
    # GitHub needs the real proxy; localhost must never use one. Separate openers.
    return urllib.request.build_opener()


def _local_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_serving(base_url: str, timeout: int = 3) -> bool:
    """True when something healthy is already answering on ``base_url``."""
    try:
        with _local_opener().open(f"{base_url.rstrip('/')}/health", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")).get("status") == "ok"
    except Exception:  # noqa: BLE001 - not up, still loading, or not ours
        return False


_PROCESS: Optional[subprocess.Popen] = None
# On Windows a child process outlives its parent, so without this hook closing
# FreeCAD would orphan a multi-gigabyte llama-server. Registered on first start.
_ATEXIT_REGISTERED = False


def start(model_path: str, base_url: str, *, port: int,
          on_log: Optional[Callable[[str], None]] = None,
          context_tokens: int = 8192,
          ready_timeout: float = 900.0) -> str:
    """Start llama-server for ``model_path``; return a human-readable status.

    Reuses a healthy server on the port instead of loading the weights twice.
    Fetches the backend if this machine has none.
    """
    global _PROCESS
    log = on_log or (lambda _line: None)

    if is_serving(base_url):
        return f"Reusing the model already serving at {base_url}"
    if not os.path.isfile(model_path):
        raise BackendError(f"Model file not found:\n    {model_path}")

    binary = find_server() or fetch_server(on_log)

    argv = [binary, "-m", model_path, "--host", _host_of(base_url), "--port", str(port)]
    # Cap the context instead of taking the model's maximum. Left to itself,
    # llama-server allocates a KV cache for the full trained window - 32k+ on a
    # modern model - which costs gigabytes of RAM and slows every request, for a
    # window a CAD prompt never comes close to using. `machine serve` caps it
    # too (its activation report shows ctx 4096); matching that is what keeps a
    # directly-spawned backend as quick as one behind the CLI.
    argv += ["--ctx-size", str(context_tokens)]
    threads = os.cpu_count() or 4
    argv += ["--threads", str(max(1, min(threads, 8)))]
    log(f"Loading {os.path.basename(model_path)}…")
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
              "stdin": subprocess.DEVNULL, "text": True, "bufsize": 1}
    if sys.platform == "win32":
        # Own process group, so a console Ctrl-C does not race our shutdown.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["startupinfo"] = _hidden_window()
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(argv, **kwargs)
    except OSError as exc:
        raise BackendError(f"Could not run the inference backend {binary}: {exc}") from exc
    _PROCESS = process
    global _ATEXIT_REGISTERED
    if not _ATEXIT_REGISTERED:
        atexit.register(stop)
        _ATEXIT_REGISTERED = True
    _pump(process, log)

    deadline = time.time() + ready_timeout
    while time.time() < deadline:
        if process.poll() is not None:
            _PROCESS = None
            raise BackendError(
                f"The inference backend exited (code {process.returncode}) while "
                f"loading:\n    {model_path}\n\n"
                "A corrupt or unsupported .gguf is the usual cause; the FreeCAD "
                "report view has the backend's own output."
            )
        if is_serving(base_url):
            return f"Loaded {os.path.basename(model_path)} — serving at {base_url}"
        time.sleep(1.0)

    stop()
    raise BackendError(
        f"The model did not finish loading within {ready_timeout:.0f}s:\n    {model_path}"
    )


def stop() -> bool:
    """Stop a backend we started. True if one was running."""
    global _PROCESS
    process, _PROCESS = _PROCESS, None
    if process is None or process.poll() is not None:
        return False
    process.terminate()
    try:
        process.wait(timeout=10)
        return True
    except subprocess.TimeoutExpired:
        pass
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        process.kill()
    return True


def started_here() -> bool:
    return _PROCESS is not None and _PROCESS.poll() is None


def _pump(process, log) -> None:
    """Drain the backend's output so a full pipe can never block it."""
    import threading

    def run() -> None:
        try:
            for line in process.stdout:
                log(line.rstrip())
        except (ValueError, OSError):
            pass

    threading.Thread(target=run, daemon=True).start()


def _hidden_window():
    """Keep llama-server's console window from flashing over FreeCAD."""
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return info


def _host_of(base_url: str) -> str:
    import urllib.parse

    return urllib.parse.urlparse(base_url).hostname or "127.0.0.1"
