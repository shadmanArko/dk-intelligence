"""Model plug-ins: the ONE contract every model implements (ADR 0009).

A model is a folder `intel/models/<name>/` with a `plugin.py` that defines `PLUGIN = <ModelPlugin subclass>()`.
Everything else finds it through `discover()`:

- the Sunday run (`intel.weekly`) runs each plug-in's step in `order`;
- the pipeline diagram (`intel.pipeline`) gets its step, box and arrows;
- packaging (`intel.package`) seals the source files the plug-in lists for each model it produces;
- Telegram uses its approval text.

Adding a model = one new folder; no other file changes (open/closed). `plugin.py` must stay light: import heavy
libraries (pandas, sklearn, ...) inside `run`, because the VPS loads every plug-in for its texts.
"""

from __future__ import annotations

import importlib
import pkgutil
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any, ClassVar, Literal

Input = Literal["pull", "market", "ledger"]
Kind = Literal["scorer", "insights"]
INPUTS: tuple[Input, ...] = ("pull", "market", "ledger")
PKG_ROOT = Path(__file__).parent


@dataclass(frozen=True)
class RunContext:
    """What a plug-in may use during the Sunday run. Snapshots are immutable folders (pull / market)."""

    pull: Path
    market: Path
    ledger: list[dict[str, Any]] | None
    runs_dir: Path
    checks_dir: Path
    mlflow_uri: str | None
    log: Callable[[str], None] = print


@dataclass(frozen=True)
class StepOutcome:
    """Result of one plug-in step: the run folders it produced (one per model) and what the progress page shows."""

    run_dirs: list[Path]
    note: str
    details: dict[str, Any]
    headline: dict[str, Any] = field(default_factory=dict)  # fields merged into the weekly run report


@dataclass(frozen=True)
class Box:
    """Where the step sits in the pipeline diagram (Mac lane)."""

    col: int
    row: int
    label: str
    sub: str | None = None


class ModelPlugin(ABC):
    step: ClassVar[str]  # pipeline step key, e.g. "community"
    order: ClassVar[int]  # position in the Sunday run (lower first)
    label: ClassVar[str]  # step title
    desc: ClassVar[str]  # what the step does, in plain words (diagram + Telegram /training)
    inputs: ClassVar[tuple[Input, ...]]  # which snapshots it reads (arrows in the diagram)
    box: ClassVar[Box]
    kind: ClassVar[Kind]
    models: ClassVar[dict[str, tuple[str, ...]]]  # model name -> files (relative to intel/) sealed into its package

    @abstractmethod
    def run(self, ctx: RunContext) -> StepOutcome:
        """Train / compute, judge, and write one run folder per model (report.json, model.joblib, ...)."""

    def scores(self, report: dict[str, Any]) -> tuple[float | None, float | None]:
        """(new, current) headline number for the run report; None when the model has no single score."""
        return None, None

    def approval_text(self, meta: dict[str, Any], approval_id: int, public_url: str | None) -> str | None:
        """Telegram text asking for approval; None = the generic text for its kind."""
        return None


def insights_approval(icon: str, title: str, meta: dict[str, Any], public_url: str | None, ask: str) -> str:
    """The common shape of an insights approval message: title, version, its top lines, a question, a link."""
    tops = "\n".join(f"• {escape(str(t))}" for t in meta.get("top_complaints") or [])
    link = f'\n\n<a href="{escape(public_url)}/insights">Open Insights</a>' if public_url else ""
    return f"{icon} <b>{escape(title)}</b> <code>{escape(str(meta['version']))}</code>\n{tops}\n\n{ask}{link}"


class PluginError(RuntimeError):
    pass


def validate(plugins: Sequence[ModelPlugin]) -> None:
    """Fail at start-up, not in the middle of a Sunday run: unique steps / orders / model names, files exist."""
    keys: dict[str, list[str]] = {
        "step": [p.step for p in plugins],
        "order": [str(p.order) for p in plugins],
        "model name": [n for p in plugins for n in p.models],
    }
    for what, xs in keys.items():
        if dup := sorted({x for x in xs if xs.count(x) > 1}):
            raise PluginError(f"duplicate plug-in {what}: {dup}")
    for p in plugins:
        if not p.models:
            raise PluginError(f"plug-in {p.step!r} produces no model")
        if bad := [i for i in p.inputs if i not in INPUTS]:
            raise PluginError(f"plug-in {p.step!r} has unknown inputs {bad}")
        for name, files in p.models.items():
            if missing := [f for f in files if not (PKG_ROOT / f).is_file()]:
                raise PluginError(f"model {name!r}: source files not found {missing}")


@lru_cache
def discover() -> tuple[ModelPlugin, ...]:
    """Every `intel.models.<name>.plugin.PLUGIN`, validated, in run order."""
    import intel.models

    found: list[ModelPlugin] = []
    for m in pkgutil.iter_modules(intel.models.__path__):
        if not m.ispkg:
            continue
        try:
            mod = importlib.import_module(f"intel.models.{m.name}.plugin")
        except ModuleNotFoundError as e:
            if e.name == f"intel.models.{m.name}.plugin":
                continue  # a helper package without a plug-in (e.g. shared code)
            raise
        plugin = getattr(mod, "PLUGIN", None)
        if not isinstance(plugin, ModelPlugin):
            raise PluginError(f"intel.models.{m.name}.plugin must define PLUGIN = <ModelPlugin instance>")
        found.append(plugin)
    found.sort(key=lambda p: p.order)
    validate(found)
    covered = {n for p in found for n in p.models}
    orphans = sorted(d.parent.name for d in (PKG_ROOT / "models").glob("*/spec.yaml") if d.parent.name not in covered)
    if orphans:
        raise PluginError(f"model folders with a spec.yaml but no plug-in producing them: {orphans}")
    return tuple(found)


def model_sources(plugins: Sequence[ModelPlugin] | None = None) -> dict[str, tuple[str, ...]]:
    """model name -> the files sealed into its package (and hash-checked by the server)."""
    return {name: files for p in (plugins or discover()) for name, files in p.models.items()}


def plugin_for_model(name: str, plugins: Sequence[ModelPlugin] | None = None) -> ModelPlugin | None:
    return next((p for p in (plugins or discover()) if name in p.models), None)
