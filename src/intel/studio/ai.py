"""The one door to AI inside a run: budget first, then the runner, then the books (tokens per agent and per idea)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from intel.studio.budget import Budget
from intel.studio.ports import AgentResult, RunnerPort
from intel.studio.runlog import RunLog


class AI:
    def __init__(self, runner: RunnerPort, budget: Budget, log: RunLog,
                 on_alert: Callable[[int, int], None] | None = None) -> None:  # fmt: skip
        self.runner, self.budget, self.log = runner, budget, log
        self.on_alert = on_alert  # told once when the budget reaches its alert level: (tokens used, the cap)
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def call(
        self,
        node: str,
        agent: str,
        task: dict[str, Any],
        idea_id: str | None = None,
        valid: Callable[[dict[str, Any]], bool] | None = None,
        files: dict[str, Path] | None = None,
    ) -> AgentResult:
        """Run one agent unless the budget says no. An answer that is missing, not JSON, or fails `valid` is asked for
        once more (found in a real run: 1 call in 40 answered without JSON). A refused call is an AgentResult with no
        answer and the reason in `error` (it starts with 'budget:'), so the caller reports the idea, never loses it."""
        result = self._once(node, agent, task, idea_id, files)
        usable = result.answer is not None and (valid is None or valid(result.answer))
        if not usable and not result.error.startswith("budget:"):
            self.log.emit(node, "retry", reason=result.error or "the answer was not usable")
            result = self._once(f"{node}-retry", agent, task, idea_id, files)
        return result

    def _once(
        self, node: str, agent: str, task: dict[str, Any], idea_id: str | None, files: dict[str, Path] | None = None
    ) -> AgentResult:
        why = self.budget.allow(idea_id)
        if why:
            self.log.emit(node, "budget_stop", agent=agent, reason=why)
            return AgentResult(None, error=f"budget: {why}")
        result = self.runner.run(node, agent, json.dumps(task, ensure_ascii=False), files)
        if self.budget.charge(result.tokens, idea_id):
            self.log.emit("orchestrator", "budget_alert", used=self.budget.used, cap=self.budget.max_tokens,
                          share=round(self.budget.share, 3))  # fmt: skip
            if self.on_alert:
                self.on_alert(self.budget.used, self.budget.max_tokens)
        with self._lock:
            self.calls.append({"node": node, "agent": agent, "idea": idea_id, "tokens": result.tokens,
                               "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
                               "seconds": round(result.seconds, 1), "ok": result.answer is not None})  # fmt: skip
        return result
