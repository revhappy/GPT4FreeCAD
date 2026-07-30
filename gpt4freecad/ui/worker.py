"""Background worker so the LLM network call never freezes FreeCAD's UI.

Only the *generation* (network + validation) runs off-thread; building geometry
happens back on the main thread via signals, because FreeCAD document mutation
must not happen from a worker thread.
"""

from __future__ import annotations

from typing import Callable, Optional

from .qt import QtCore


class LLMWorker(QtCore.QThread):
    """Runs ``fn()`` on a thread and emits the result or the error message."""

    succeeded = QtCore.Signal(object)  # GenerationResult
    failed = QtCore.Signal(str)

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self._fn = fn
        # The exception itself, kept alongside the message: a caller deciding
        # whether to auto-repair needs the *kind* of failure. A bad plan is worth
        # sending back to the model; a wrong API key or a dead network is not.
        self.error: Optional[BaseException] = None

    def run(self):  # noqa: D401 - QThread entry point
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self.error = exc
            self.failed.emit(str(exc))
            return
        self.error = None
        self.succeeded.emit(result)
