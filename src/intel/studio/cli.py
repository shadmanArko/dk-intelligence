"""`uv run intel studio …`: run the content studio, see a run, and measure a version on frozen inputs."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import typer

from intel.studio import config as studio_config
from intel.studio import handover, media
from intel.studio import schedule as schedule_mod
from intel.studio.agents import ClaudeRunner
from intel.studio.config import REPO, StudioConfig
from intel.studio.mcp import McpHub, server_specs
from intel.studio.notify import LocalNotifier, NullNotifier
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


def _models(cfg: StudioConfig, overrides: list[str] | None) -> dict[str, str]:
    """models.yaml, then any `--model agent=name` from the command line (an experiment on frozen inputs)."""
    models: dict[str, str] = dict(cfg.models.agents)
    for item in overrides or []:
        agent, _, name = item.partition("=")
        if agent not in models or name not in ("sonnet", "haiku", "opus"):
            typer.echo(
                f"--model {item!r}: use agent=sonnet|haiku|opus with an agent from {', '.join(models)}", err=True
            )
            raise typer.Exit(2)
        models[agent] = name
    return models


def build(cfg: StudioConfig, need_publisher: bool, model_overrides: list[str] | None = None) -> tuple[Studio, McpHub]:
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
        make_runner=lambda log: ClaudeRunner(log, hub, servers, cfg.budgets.max_turns, cfg.budgets.agent_timeout_s,
                                             models=_models(cfg, model_overrides)),
        renderer=ReelsRenderer(reels, cfg.budgets.render_timeout_s), publisher=publisher, data_dir=DATA,
        notifier=LocalNotifier(DATA) if cfg.schedule.notify else NullNotifier(),
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
    model: list[str] = typer.Option(
        [], "--model", help="run an agent on another model, e.g. designer=haiku (an experiment)"
    ),
    if_due: bool = typer.Option(
        False,
        "--if-due",
        help="only run when the calendar needs filling (what the schedule uses); uses the schedule's publish mode",
    ),
) -> None:
    """Plan, write, design, render and hand over. Every idea that does not make it is reported with its reason."""
    if publish not in MODES:
        typer.echo(f"--publish must be one of {', '.join(MODES)}", err=True)
        raise typer.Exit(2)
    cfg = _load(days, ideas_per_day, platforms, config_dir)
    if if_due:
        publish = cfg.schedule.publish
    studio, hub = build(cfg, need_publisher=publish != "render" or if_due, model_overrides=model)
    if if_due:
        assert isinstance(studio.publisher, handover.PublishClient)
        overview = studio.publisher.overview(days_back=1, days_ahead=30)
        mine = studio.publisher.calendar(source="api", limit=200).get("posts") or []
        ok, why = schedule_mod.due(cfg.schedule, overview, schedule_mod.last_scheduled_run(DATA / "runs"),
                                   dt.datetime.now(dt.UTC), schedule_mod.agent_plan_ends(mine))  # fmt: skip
        typer.echo(f"[{dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M}Z] {'RUN' if ok else 'skip'}: {why}")
        if not ok:
            hub.close()
            raise typer.Exit(0)
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


schedule_app = typer.Typer(no_args_is_help=True, help="Running without you: the daily check on this Mac")
studio_app.add_typer(schedule_app, name="schedule")


@schedule_app.command("check")
def schedule_check() -> None:
    """Would a scheduled run start now? Reads the publisher's calendar; changes nothing, spends no tokens."""
    cfg = _load(None, None, None, None)
    env = handover.load_env()
    if "DK_PUBLISH_URL" not in env or "DK_PUBLISH_KEY" not in env:
        typer.echo("set DK_PUBLISH_URL and DK_PUBLISH_KEY in .env", err=True)
        raise typer.Exit(1)
    client = handover.PublishClient(env["DK_PUBLISH_URL"], env["DK_PUBLISH_KEY"])
    overview = client.overview(days_back=1, days_ahead=30)
    mine = client.calendar(source="api", limit=200).get("posts") or []
    last = schedule_mod.last_scheduled_run(DATA / "runs")
    ok, why = schedule_mod.due(
        cfg.schedule, overview, last, dt.datetime.now(dt.UTC), schedule_mod.agent_plan_ends(mine)
    )
    typer.echo(("WOULD RUN: " if ok else "would skip: ") + why)


@schedule_app.command("install")
def schedule_install() -> None:
    """Install the daily check (launchd, this user only). It makes DRAFTS only; you still approve every post."""
    import shutil

    cfg = _load(None, None, None, None)
    uv = shutil.which("uv")
    if uv is None:
        typer.echo("uv is not on the PATH", err=True)
        raise typer.Exit(1)
    path = schedule_mod.install(cfg.schedule, uv, f"{Path(uv).parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    typer.echo(f"installed {path}: a check every day at {cfg.schedule.run_at}. Log: {DATA / 'schedule.log'}")
    typer.echo("The Mac must be on (not asleep) at that time. Remove it with: uv run intel studio schedule uninstall")


@schedule_app.command("uninstall")
def schedule_uninstall() -> None:
    """Remove the daily check."""
    typer.echo("removed" if schedule_mod.uninstall() else "it was not installed")


@schedule_app.command("status")
def schedule_status() -> None:
    """Is the daily check installed, and what did it last do?"""
    typer.echo(f"installed: {schedule_mod.PLIST.exists()} ({schedule_mod.PLIST})")
    log = DATA / "schedule.log"
    typer.echo(
        "last lines of the log:\n" + "\n".join(log.read_text().splitlines()[-5:]) if log.exists() else "no log yet"
    )


@studio_app.command("remind")
def remind_cmd(show: bool = typer.Option(False, "--show", help="print what is waiting instead of notifying")) -> None:
    """The daily reminder: a Mac notification ONLY when something waits for you (what the Today page lists)."""
    from intel.studio import console, today

    cfg = _load(None, None, None, None)
    data = today.build(console._readers(), cfg.schedule, today.owner_decisions())  # noqa: SLF001
    message = today.reminder_text(data)
    if show or message is None:
        typer.echo("\n".join(f"[{i['level']}] {i['title']}" for i in data["items"]))
    if message is not None and not show:
        LocalNotifier(DATA).notify(*message)


def autonomy_cmd() -> None:
    """Which kinds of post you approve most (measurement only: nothing is ever approved for you)."""
    from intel.studio import autonomy

    cfg = _load(None, None, None, None)
    env = handover.load_env()
    if "DK_PUBLISH_URL" not in env or "DK_PUBLISH_KEY" not in env:
        typer.echo("set DK_PUBLISH_URL and DK_PUBLISH_KEY in .env", err=True)
        raise typer.Exit(1)
    client = handover.PublishClient(env["DK_PUBLISH_URL"], env["DK_PUBLISH_KEY"])
    rows = client.calendar(source="api", limit=200).get("posts") or []
    states = [json.loads(p.read_text()) for p in sorted((DATA / "runs").glob("*/state.json"))]
    table = autonomy.table(rows, autonomy.idea_kinds(states), cfg.autonomy, dt.datetime.now(dt.UTC))
    if not table:
        typer.echo(
            "no decided studio posts in the last "
            f"{cfg.autonomy.window_days} days: approve or reject some on the review page first"
        )
        return
    typer.echo(
        f"{'kind':<10}{'value':<16}{'decided':>8}{'approved':>9}{'rate':>7}  clears the bar "
        f"({cfg.autonomy.approval_rate:.0%} of at least {cfg.autonomy.min_posts})"
    )
    for r in table:
        typer.echo(
            f"{r['kind']:<10}{r['value']:<16}{r['decided']:>8}{r['approved']:>9}{r['rate']:>7.0%}  "
            f"{'yes' if r['clears_bar'] else 'no'}"
        )
    typer.echo("Autonomy is OFF and not built: this is a measurement (ADR 0037).")


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
