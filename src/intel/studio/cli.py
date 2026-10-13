"""`uv run intel studio …`: run the content studio, see a run, and measure a version on frozen inputs."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import typer

from intel.studio import config as studio_config
from intel.studio import handover, media
from intel.studio.agents import ClaudeRunner
from intel.studio.config import REPO, StudioConfig
from intel.studio.mcp import McpHub, server_specs
from intel.studio.render import ReelsRenderer
from intel.studio.run import MODES, Options, Studio

studio_app = typer.Typer(no_args_is_help=True, add_completion=False, help="The content studio: 150 posts per 10 days")
eval_app = typer.Typer(no_args_is_help=True, help="Measure a version on frozen inputs: quality next to cost")
studio_app.add_typer(eval_app, name="eval")
DATA = REPO / "data" / "studio"


def _load(days: int | None, ideas_per_day: int | None, platforms: str | None, config_dir: Path | None) -> StudioConfig:
    try:
        cfg = studio_config.load(config_dir or studio_config.CONFIG_DIR)
        return cfg.with_plan(horizon_days=days, ideas_per_day=ideas_per_day,
                             platforms=[p.strip() for p in platforms.split(",")] if platforms else None)  # fmt: skip
    except studio_config.StudioConfigError as e:
        typer.echo(f"settings: {e}", err=True)
        raise typer.Exit(2) from e


def build(cfg: StudioConfig, need_publisher: bool) -> tuple[Studio, McpHub]:
    """The real ports: MCP over stdio, headless Claude Code, the reels CLI, the publisher's HTTP API."""
    reels = media.REELS
    if not (reels / "package.json").exists():
        typer.echo(f"the reels engine is not at {reels} (set DK_REELS_DIR)", err=True)
        raise typer.Exit(1)
    env = handover.load_env()
    publisher = None
    if "DK_PUBLISH_URL" in env and "DK_PUBLISH_KEY" in env:
        publisher = handover.PublishClient(env["DK_PUBLISH_URL"], env["DK_PUBLISH_KEY"])
    elif need_publisher:
        typer.echo(
            "set DK_PUBLISH_URL and DK_PUBLISH_KEY (a key with the 'draft' right) in .env; never printed", err=True
        )
        raise typer.Exit(1)
    servers = server_specs(reels)
    hub = McpHub(servers)
    studio = Studio(
        cfg=cfg, tools=hub,
        make_runner=lambda log: ClaudeRunner(log, hub, servers, cfg.budgets.max_turns, cfg.budgets.agent_timeout_s),
        renderer=ReelsRenderer(reels, cfg.budgets.render_timeout_s), publisher=publisher, data_dir=DATA,
    )  # fmt: skip
    return studio, hub


@studio_app.command("run")
def run(
    publish: str = typer.Option(
        "render",
        help="render: media only, nothing sent · dry-run: validate with the "
        "publisher, create nothing · drafts: create drafts for you to approve",
    ),
    days: int | None = typer.Option(None, help="days to plan (default: plan.yaml)"),
    ideas_per_day: int | None = typer.Option(None, help="ideas per day (default: plan.yaml)"),
    platforms: str | None = typer.Option(None, help="comma-separated, e.g. instagram,facebook"),
    inputs: Path | None = typer.Option(None, help="replay frozen inputs instead of fetching"),
    save_inputs: Path | None = typer.Option(None, help="freeze this run's inputs to a file"),
    resume: str | None = typer.Option(None, help="continue a stopped run by its id"),
    suffix: str = typer.Option("", help="redo ideas whose post was rejected: a new key, e.g. r2"),
    trigger: str = typer.Option("manual", help="manual | button | schedule | eval"),
    config_dir: Path | None = typer.Option(None, help="settings folder (default: config/studio)"),
) -> None:
    """Plan, write, design, render and hand over. Every idea that does not make it is reported with its reason."""
    if publish not in MODES:
        typer.echo(f"--publish must be one of {', '.join(MODES)}", err=True)
        raise typer.Exit(2)
    cfg = _load(days, ideas_per_day, platforms, config_dir)
    studio, hub = build(cfg, need_publisher=publish != "render")
    try:
        code = studio.run(Options(publish=publish, trigger=trigger, inputs=inputs, save_inputs=save_inputs,
                                  resume=resume, suffix=suffix))  # fmt: skip
    finally:
        hub.close()
    raise typer.Exit(code)


def _runs() -> list[Path]:
    return sorted((p for p in (DATA / "runs").glob("*") if (p / "state.json").exists()), reverse=True)


@studio_app.command("status")
def status(run_id: str | None = typer.Argument(None, help="a run id (default: the latest)")) -> None:
    """The result of a run: ideas, posts, tokens, minutes, and what was held and why."""
    runs = _runs()
    folder = DATA / "runs" / run_id if run_id else (runs[0] if runs else None)
    if folder is None or not folder.exists():
        typer.echo("no runs yet: `uv run intel studio run`")
        raise typer.Exit(1)
    report = folder / "report.md"
    typer.echo(report.read_text() if report.exists() else f"{folder.name}: still running, or stopped (resume it)")


@studio_app.command("curate")
def curate_cmd(
    scope: str = typer.Option("new", help="new: files with no knowledge yet · enrich: catalogued files without mood"),
    limit: int = typer.Option(5, min=1, max=20, help="items per run (about 8k tokens each)"),
) -> None:
    """The footage curator looks at contact sheets and PROPOSES knowledge for new or unlabelled media. Nothing
    is accepted here: you approve in the reels repo with `npm run curate -- --list` and `--accept-all`."""
    from intel.studio import curator as curator_mod
    from intel.studio.ai import AI
    from intel.studio.budget import Budget
    from intel.studio.runlog import RunLog

    cfg = _load(None, None, None, None)
    studio, hub = build(cfg, need_publisher=False)
    try:
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        log = RunLog(DATA / "curation" / stamp, stamp)
        ai = AI(studio.make_runner(log), Budget(cfg.budgets.max_tokens_per_run, cfg.budgets.alert_at, 99), log)
        report = curator_mod.curate(hub, ai, scope, limit)
        report["tokens"] = ai.budget.used
    finally:
        hub.close()
    typer.echo(json.dumps(report, indent=1, ensure_ascii=False))
    if report["proposed"]:
        typer.echo(
            "Proposed, not accepted. In the reels repo: npm run curate -- --list, then --accept-all (or --accept <id>)."
        )


@studio_app.command("requests")
def requests_cmd() -> None:
    """What the creative director and the designer said no template could do, most asked first: the next templates to
    build (a person builds them in the reels repo, under its gates)."""
    from intel.studio import requests as style_requests

    rows = style_requests.read()
    if not rows:
        typer.echo("no style requests yet")
        return
    for text, n in style_requests.tally(rows)[:20]:
        typer.echo(f"{n:>3}x  {text}")


# ---------- eval ----------

EVAL = DATA / "eval"


@eval_app.command("freeze")
def freeze(days: int | None = None, ideas_per_day: int | None = None, platforms: str | None = None) -> None:
    """Freeze today's inputs (0 tokens) so versions can be compared on exactly the same plan."""
    from intel.studio.inputs import fetch_inputs

    cfg = _load(days, ideas_per_day, platforms, None)
    studio, hub = build(cfg, need_publisher=False)
    try:
        data = fetch_inputs(hub, cfg, dt.datetime.now(dt.UTC).astimezone(studio._tz()).date())  # noqa: SLF001
        if studio.publisher is not None:
            data["publish_spec"] = studio.publisher.spec(cache=DATA / "publish-spec.json")
    finally:
        hub.close()
    EVAL.mkdir(parents=True, exist_ok=True)
    (EVAL / "inputs.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    typer.echo(f"frozen: {EVAL / 'inputs.json'}")


@eval_app.command("run")
def eval_run(label: str = typer.Option(..., help="a name for this version, e.g. v1"),
             publish: str = "render", days: int | None = None, ideas_per_day: int | None = None,
             platforms: str | None = None) -> None:  # fmt: skip
    """Run on the frozen inputs, then record quality (posts made) next to cost (tokens, minutes)."""
    frozen = EVAL / "inputs.json"
    if not frozen.exists():
        typer.echo("no frozen inputs: run `uv run intel studio eval freeze` first", err=True)
        raise typer.Exit(1)
    cfg = _load(days, ideas_per_day, platforms, None)
    studio, hub = build(cfg, need_publisher=publish != "render")
    before = {p.name for p in _runs()}
    try:
        code = studio.run(Options(publish=publish, trigger="eval", inputs=frozen))
    finally:
        hub.close()
    new = [p for p in _runs() if p.name not in before]
    if new and (new[0] / "summary.json").exists():
        s = json.loads((new[0] / "summary.json").read_text())
        row = {"label": label, "run": s["run_id"], "publish": publish, "ideas": s["ideas"], "variants": s["variants"],
               "tokens": s["tokens"]["total"], "per_idea": s["tokens"]["per_idea"], "seconds": s["seconds"]["total"],
               "held": s["variants"]["held_reasons"]}  # fmt: skip
        with (EVAL / "results.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        typer.echo(json.dumps(row, indent=1))
    raise typer.Exit(code)


@eval_app.command("show")
def show() -> None:
    """The comparison table: one row per recorded run."""
    path = EVAL / "results.jsonl"
    rows: list[dict[str, Any]] = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    typer.echo(f"{'version':<14}{'mode':<9}{'ideas':>8}{'posts':>10}{'tokens':>10}{'per idea':>10}{'minutes':>9}")
    for r in rows:
        ideas = f"{r['ideas']['completed']}/{r['ideas']['planned']}"
        posts = f"{r['variants']['made']}/{r['variants']['planned']}"
        typer.echo(f"{r['label']:<14}{r['publish']:<9}{ideas:>8}{posts:>10}{r['tokens']:>10,}{r['per_idea']:>10,}"
                   f"{r['seconds'] / 60:>9.1f}")  # fmt: skip
