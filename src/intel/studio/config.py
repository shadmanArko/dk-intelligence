"""The studio's settings as data: `config/studio/{plan,slots,routes,budgets}.yaml`, validated once at load.

A typo in a YAML file stops the run before any AI is spent (unknown keys are refused). The loaded settings are
frozen into the run folder (`config.json`) so a result can be traced to the settings that made it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

REPO = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO / "config" / "studio"
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
IdeaFormat = str  # a key of routes.yaml `formats` (checked against it when the settings load)


class StudioConfigError(Exception):
    """A settings file is wrong. The message names the file and the line to fix."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pillar(_Strict):
    name: str
    about: str


class PlanCfg(_Strict):
    horizon_days: int = Field(ge=1, le=30)
    first_day_offset: int = Field(ge=0, le=7)
    ideas_per_day: int = Field(ge=1, le=3)
    max_ideas_per_run: int = Field(ge=1, le=90)
    chunk_ideas: int = Field(ge=1, le=20)
    platforms: list[str] = Field(min_length=1)
    primary_platform: str
    format_mix: list[IdeaFormat] = Field(min_length=1)
    format_rotation: list[list[IdeaFormat]] = []  # optional: day 1 uses row 1, day 2 row 2, ... then repeats
    max_pillar_share: float = Field(gt=0, le=1)
    pillars: list[Pillar] = Field(min_length=1)

    @model_validator(mode="after")
    def _primary_is_posted_to(self) -> PlanCfg:
        if self.primary_platform not in self.platforms:
            raise ValueError(f"primary_platform {self.primary_platform!r} is not in platforms")
        return self


class SlotsCfg(_Strict):
    timezone: str
    windows: list[str] = Field(min_length=1)
    times: dict[str, dict[str, str]]

    @field_validator("times")
    @classmethod
    def _clock_times(cls, v: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        for platform, by_window in v.items():
            for window, hhmm in by_window.items():
                if not HHMM.match(hhmm):
                    raise ValueError(f"times.{platform}.{window}: {hhmm!r} is not HH:MM")
        return v


class FormatRoute(_Strict):
    make: Literal["video", "carousel", "image", "story"]
    render: tuple[str, str]
    content_type: Literal["image", "video", "reel", "carousel", "short"]
    targets: dict[str, str]
    gate: bool = True  # False: the scorer ranks the windows but never rules the slot out (not trained on stories)


class RoutesCfg(_Strict):
    formats: dict[str, FormatRoute]
    defaults: dict[str, dict[str, Any]] = {}
    adapter_options: dict[str, list[str]] = {}
    feel: dict[str, str] = {}
    reviewed_languages: list[str] = []


class BudgetsCfg(_Strict):
    max_tokens_per_run: int = Field(ge=10_000)
    alert_at: float = Field(gt=0, lt=1)
    max_turns: dict[str, int]
    max_ai_calls_per_idea: int = Field(ge=3)
    parallel_ideas: int = Field(ge=1, le=8)
    agent_timeout_s: int = Field(ge=30)
    render_timeout_s: int = Field(ge=60)


class DirectorCfg(_Strict):
    max_template_share: float = Field(gt=0, le=1)  # no template may take more than this share of the plan
    recent_window: int = Field(ge=0, le=100)  # how many earlier posts' templates the director is told about
    defaults: dict[str, str]  # family (video | slides) → the template used when the registry or the director gives none


class CraftCfg(_Strict):
    director: DirectorCfg
    platforms: dict[str, dict[str, Any]]  # per platform: how each kind of post is made there (craft, not rules)


class ScheduleCfg(_Strict):
    enabled: bool
    run_at: str
    publish: Literal["drafts", "dry-run", "render"]
    lead_days: int = Field(ge=1, le=30)
    min_days_between: int = Field(ge=1, le=30)
    max_awaiting: int = Field(ge=1, le=500)
    notify: bool

    @field_validator("run_at")
    @classmethod
    def _clock(cls, v: str) -> str:
        if not HHMM.match(v):
            raise ValueError(f"run_at {v!r} is not HH:MM")
        return v


class AutonomyCfg(_Strict):
    enabled: bool
    approval_rate: float = Field(gt=0, le=1)
    window_days: int = Field(ge=1, le=90)
    min_posts: int = Field(ge=1)
    kinds: list[Literal["pillar", "format", "template"]] = Field(min_length=1)


class ModelsCfg(_Strict):
    agents: dict[str, Literal["sonnet", "haiku", "opus"]]


class StudioConfig(_Strict):
    plan: PlanCfg
    slots: SlotsCfg
    routes: RoutesCfg
    budgets: BudgetsCfg
    craft: CraftCfg
    schedule: ScheduleCfg
    autonomy: AutonomyCfg
    models: ModelsCfg

    @model_validator(mode="after")
    def _consistent(self) -> StudioConfig:
        p = self.plan
        if p.ideas_per_day > len(self.slots.windows):
            raise ValueError(f"ideas_per_day {p.ideas_per_day} is more than the {len(self.slots.windows)} windows")
        for platform in p.platforms:
            missing = [w for w in self.slots.windows if w not in self.slots.times.get(platform, {})]
            if missing:
                raise ValueError(f"slots.yaml: {platform} has no time for the window(s) {', '.join(missing)}")
        for fmt in [*p.format_mix, *(f for row in p.format_rotation for f in row)]:
            if fmt not in self.routes.formats:
                raise ValueError(f"format_mix / format_rotation names {fmt!r}, which routes.yaml does not define")
        for row in p.format_rotation:
            if len(row) != p.ideas_per_day:
                raise ValueError(f"every format_rotation row needs {p.ideas_per_day} formats, not {len(row)}")
        for agent in ("strategist", "copywriter", "designer", "adapter"):
            if agent not in self.budgets.max_turns:
                raise ValueError(f"budgets.yaml: max_turns has no entry for {agent}")
        return self

    # ---- derived views ----

    def platforms_for(self, fmt: str) -> list[str]:
        """The platforms one idea of this format is posted to: the plan's platforms that the format has a route for."""
        targets = self.routes.formats[fmt].targets
        return [p for p in self.plan.platforms if p in targets]

    def routes_table(self) -> dict[tuple[str, str], dict[str, str]]:
        """(render kind, size) → platform → publishing format, the shape the handover reads."""
        table: dict[tuple[str, str], dict[str, str]] = {}
        for route in self.routes.formats.values():
            table.setdefault(route.render, {}).update(route.targets)
        return table

    def handover_cfg(self) -> dict[str, Any]:
        return {
            "reviewed_languages": list(self.routes.reviewed_languages),
            "defaults": {k: dict(v) for k, v in self.routes.defaults.items()},
            "routes": self.routes_table(),
        }

    def with_plan(self, **changes: Any) -> StudioConfig:
        """The same settings with some plan fields changed (command-line overrides); validated again."""
        data = self.model_dump()
        data["plan"].update({k: v for k, v in changes.items() if v is not None})
        try:
            return StudioConfig.model_validate(data)
        except ValidationError as e:
            raise StudioConfigError(str(e)) from e

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:12]

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = json.loads(self.model_dump_json())
        out["digest"] = self.digest()
        return out


def load(directory: Path = CONFIG_DIR) -> StudioConfig:
    parts: dict[str, Any] = {}
    for name in ("plan", "slots", "routes", "budgets", "craft", "schedule", "autonomy", "models"):
        path = directory / f"{name}.yaml"
        if not path.exists():
            raise StudioConfigError(f"{path} is missing")
        try:
            parts[name] = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as e:
            raise StudioConfigError(f"{path.name}: {e}") from e
    try:
        return StudioConfig.model_validate(parts)
    except ValidationError as e:
        raise StudioConfigError(f"studio settings are not valid:\n{e}") from e
