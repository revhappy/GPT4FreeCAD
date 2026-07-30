"""Auto-repair bookkeeping shared by the panel and the engineering timeline.

Pure (no FreeCAD / Qt) so the retry policy is unit-testable. A
:class:`RepairSession` covers one user action (a Generate click, a Build click,
an AI step): it tracks how many fix-it round-trips have been spent against a
configurable budget and remembers fingerprints of plans that already failed, so
the harness stops instead of looping when the model keeps returning the same
broken program.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional, Set

DEFAULT_ROUNDS = 3


def fingerprint(payload: Any) -> str:
    """Stable fingerprint of a plan: an ops list/dict or a code string."""
    if isinstance(payload, str):
        canonical = payload.strip()
    else:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


class RepairSession:
    """Repair budget + failed-plan memory for one user action."""

    def __init__(self, budget: int = DEFAULT_ROUNDS):
        self.budget = max(int(budget), 0)
        self.attempts = 0
        self._failed: Set[str] = set()

    def reset(self, budget: Optional[int] = None) -> None:
        """Start a fresh session (new user action)."""
        if budget is not None:
            self.budget = max(int(budget), 0)
        self.attempts = 0
        self._failed.clear()

    def can_retry(self) -> bool:
        return self.attempts < self.budget

    def start_attempt(self) -> None:
        """Consume one round of the budget."""
        self.attempts += 1

    @property
    def round_label(self) -> str:
        """'2/3'-style label for status messages."""
        return f"{self.attempts}/{self.budget}"

    def note_failure(self, payload: Any) -> None:
        """Remember that this plan failed, for repeat detection."""
        self._failed.add(fingerprint(payload))

    def seen_failure(self, payload: Any) -> bool:
        """True if an identical plan already failed this session."""
        return fingerprint(payload) in self._failed
