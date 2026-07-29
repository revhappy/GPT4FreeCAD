"""Background worker so the LLM network call never freezes FreeCAD's UI.

Only the *generation* (network + validation) runs off-thread; building geometry
happens back on the main thread via signals, because FreeCAD document mutation
must not happen from a worker thread.
"""

from __future__ import annotations

from typing import Callable

from .qt import QtCore


class LLMWorker(QtCore.QThread):
    """Runs ``fn()`` on a thread and emits the result or the error message."""

    succeeded = QtCore.Signal(object)  # GenerationResult
    failed = QtCore.Signal(str)

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):  # noqa: D401 - QThread entry point
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result)
