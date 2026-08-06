"""Find GGUF models already downloaded on this machine.

The local (``machine``) provider used to offer one way to choose a model: browse
the filesystem for a ``.gguf``. Anyone running LM Studio, Ollama or llama.cpp
already has several, often many gigabytes of them, in places no one remembers.
This module goes and looks.

Pure and stdlib-only: no FreeCAD, no network, no provider imports, so the
scanning rules are unit-testable and the scan can run on a worker thread.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

# Roots to walk, relative to the user's home directory unless absolute.
# Ollama is deliberately absent: it stores weights as extensionless sha256
# blobs, so a .gguf walk finds nothing there. Its models are reachable through
# the localserver provider instead, which is the better route anyway.
_SEARCH_ROOTS = (
    (".lmstudio/models", "LM Studio"),
    (".cache/lm-studio/models", "LM Studio"),
    ("AppData/Local/nomic.ai/GPT4All", "GPT4All"),
    (".local/share/nomic.ai/GPT4All", "GPT4All"),
    (".machine", "Machine Activation"),
    (".cache/huggingface/hub", "Hugging Face"),
    ("AppData/Local/llama.cpp", "llama.cpp"),
    (".cache/llama.cpp", "llama.cpp"),
)

# Files with a .gguf extension that are not chat models.
#   mmproj-*  multimodal projectors - a companion tensor file, not a model
#   *embed*   embedding models: they return vectors, never text
_NOT_A_CHAT_MODEL = ("mmproj", "embed", "reranker", "rerank")

# Skip anything smaller than this (MB): projectors and adapters are small,
# a quantised chat model of any use is not.
_MIN_SIZE_MB = 100

# Walking a deep tree costs more than it returns; model layouts are shallow.
_MAX_DEPTH = 6


def _looks_like_chat_model(filename: str) -> bool:
    lowered = filename.lower()
    if not lowered.endswith(".gguf"):
        return False
    return not any(word in lowered for word in _NOT_A_CHAT_MODEL)


def _split_shard(filename: str) -> Optional[str]:
    """Group key for a sharded model ('...-00001-of-00003.gguf'), else None.

    A large model is split across files and only the first is passed to the
    loader; listing all of them would offer the user pieces that cannot load.
    """
    stem = filename[:-5] if filename.lower().endswith(".gguf") else filename
    marker = stem.lower().rfind("-of-")
    if marker == -1:
        return None
    head = stem[:marker]
    cut = head.rfind("-")
    return head[:cut] if cut > 0 else head


def scan_roots(roots, min_size_mb: int = _MIN_SIZE_MB) -> List[Dict]:
    """Walk ``(absolute_path, source_label)`` pairs for usable chat models.

    Returns dicts of ``path``, ``name``, ``source``, ``size_mb``, largest
    first. Unreadable directories are skipped rather than raising - a scan that
    dies on one permission error is useless.
    """
    found: Dict[str, Dict] = {}
    shards: Dict[str, str] = {}
    for root, source in roots:
        if not root or not os.path.isdir(root):
            continue
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
            if dirpath.count(os.sep) - base_depth >= _MAX_DEPTH:
                dirnames[:] = []
                continue
            for filename in filenames:
                if not _looks_like_chat_model(filename):
                    continue
                path = os.path.join(dirpath, filename)
                try:
                    size_mb = int(os.path.getsize(path) / (1024 * 1024))
                except OSError:
                    continue
                if size_mb < min_size_mb:
                    continue
                # Keep only the first shard of a split model.
                group = _split_shard(filename)
                if group is not None:
                    key = os.path.join(dirpath, group)
                    if key in shards:
                        continue
                    shards[key] = path
                found[path] = {
                    "path": path,
                    "name": filename[:-5],
                    "source": source,
                    "size_mb": size_mb,
                }
    return sorted(found.values(), key=lambda m: m["size_mb"], reverse=True)


def local_models(home: Optional[str] = None, min_size_mb: int = _MIN_SIZE_MB) -> List[Dict]:
    """Every GGUF chat model this machine already has, largest first."""
    home = home or os.path.expanduser("~")
    roots = [(root if os.path.isabs(root) else os.path.join(home, *root.split("/")),
              source)
             for root, source in _SEARCH_ROOTS]
    return scan_roots(roots, min_size_mb=min_size_mb)


def describe(model: Dict) -> str:
    """One line for the picker: name, size and where it came from."""
    size = model.get("size_mb", 0)
    size_text = f"{size / 1024:.1f} GB" if size >= 1024 else f"{size} MB"
    return f"{model.get('name', '?')}  ({size_text}, {model.get('source', '?')})"
