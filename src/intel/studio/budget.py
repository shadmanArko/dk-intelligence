"""Budget guards (playbook N4): a token cap per run, and a cap on AI calls per idea.

When the cap is reached the run stops STARTING new AI calls, finishes what is in flight, and reports every idea it did
not reach (with the reason), so nothing is dropped silently. The alert fires once, at `alert_at` of the cap.
"""

from __future__ import annotations

import threading
from collections import Counter


class Budget:
    def __init__(self, max_tokens: int, alert_at: float, max_calls_per_idea: int) -> None:
        self.max_tokens = max_tokens
        self.alert_at = alert_at
        self.max_calls = max_calls_per_idea
        self.used = 0
        self.calls: Counter[str] = Counter()
        self.alerted = False
        self._lock = threading.Lock()

    def allow(self, idea_id: str | None = None) -> str | None:
        """None when a new AI call may start, else the reason it may not."""
        with self._lock:
            if self.used >= self.max_tokens:
                return f"the run's token budget ({self.max_tokens:,}) is used up"
            if idea_id and self.calls[idea_id] >= self.max_calls:
                return f"this idea already used its {self.max_calls} AI calls"
            return None

    def charge(self, tokens: int, idea_id: str | None = None) -> bool:
        """Count one finished call. True exactly once: when the alert level is crossed."""
        with self._lock:
            self.used += tokens
            if idea_id:
                self.calls[idea_id] += 1
            if not self.alerted and self.used >= self.max_tokens * self.alert_at:
                self.alerted = True
                return True
            return False

    @property
    def share(self) -> float:
        return self.used / self.max_tokens
