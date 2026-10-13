"""`uv run intel performance …`: build the performance dataset and playbook from a pull, join our own posts."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from intel.performance import playbook
from intel.performance.build import build_dataset, idea_features, join_agent_posts
from intel.settings import get_settings

performance_app = typer.Typer(no_args_is_help=True, add_completion=False, help="What our posts actually did")


def root() -> Path:
    return get_settings().intel_data_dir / "performance"


@performance_app.command("build")
def build(pull: str = typer.Option("latest", help="a pull id, or latest")) -> None:
    """One row per post with growth-normalised results, and the playbook per platform. 0 tokens, read-only."""
    pulls = get_settings().intel_data_dir / "pulls"
    pull_id = (pulls / "LATEST").read_text().strip() if pull == "latest" else pull
    folder = pulls / pull_id
    if not folder.exists():
        typer.echo(f"no pull {pull_id}: run `uv run intel pulls` to list them", err=True)
        raise typer.Exit(1)
    data = build_dataset(folder)
    pb = playbook.build(data, pull_id)
    out = playbook.write(root(), pb, data)
    typer.echo(f"{len(data)} posts, {int(data['matured'].sum())} matured → {out}")
    typer.echo((out / "playbook.md").read_text())


@performance_app.command("join")
def join(
    run: str = typer.Option("", help="a studio run id to take what we knew about each idea from (default: all)"),
) -> None:
    """Our agent posts with their results: the publisher's published posts joined to the warehouse by the platform's
    post id. Needs DK_PUBLISH_URL and DK_PUBLISH_KEY (a read key is enough) in .env."""
    import pandas as pd

    from intel.studio import handover
    from intel.studio.config import REPO

    env = handover.load_env()
    if "DK_PUBLISH_URL" not in env or "DK_PUBLISH_KEY" not in env:
        typer.echo("set DK_PUBLISH_URL and DK_PUBLISH_KEY in .env", err=True)
        raise typer.Exit(1)
    client = handover.PublishClient(env["DK_PUBLISH_URL"], env["DK_PUBLISH_KEY"])
    rows = client.calendar(state="published", source="api", limit=200).get("posts") or []
    marker = root() / "LATEST"
    if not marker.exists():
        typer.echo("run `uv run intel performance build` first", err=True)
        raise typer.Exit(1)
    folder = root() / marker.read_text().strip()
    data = pd.read_parquet(folder / "posts.parquet")
    feats: dict[tuple[str, str], dict[str, object]] = {}
    for state_file in sorted((REPO / "data" / "studio" / "runs").glob(f"{run or '*'}/state.json")):
        feats.update(idea_features(json.loads(state_file.read_text())))
    joined = join_agent_posts(rows, data, feats)
    joined.to_parquet(folder / "agent_posts.parquet", index=False)
    typer.echo(
        f"{len(joined)} published agent posts, {int(joined['collected'].sum()) if len(joined) else 0} with results"
    )


@performance_app.command("analyst")
def analyst() -> None:
    """The weekly 'what worked' note for the owner (one AI call, no tools): written from the playbook and our own
    published posts' results. Output: analyst.md next to the playbook."""
    import pandas as pd

    from intel.studio import config as studio_config
    from intel.studio.agents import ClaudeRunner
    from intel.studio.runlog import RunLog

    marker = root() / "LATEST"
    if not marker.exists():
        typer.echo("run `uv run intel performance build` first", err=True)
        raise typer.Exit(1)
    folder = root() / marker.read_text().strip()
    pb = json.loads((folder / "playbook.json").read_text())
    agent_file = folder / "agent_posts.parquet"
    mine = pd.read_parquet(agent_file) if agent_file.exists() else pd.DataFrame()
    cfg = studio_config.load()
    compact = {p: {"posts": e["posts"], "matured": e["matured"], "note": e.get("note"), "lines": e.get("lines"),
                   "unclear": e.get("unclear", [])[:12], "expected_false_alarms": e.get("expected_false_alarms")}
               for p, e in pb["platforms"].items()}  # fmt: skip
    task = {"playbook": compact, "caveats": pb["caveats"],
            "agent_posts": json.loads(mine.head(60).to_json(orient="records", date_format="iso")) if len(mine) else [],
            "settings": {"format_mix": cfg.plan.format_mix, "windows": cfg.slots.windows,
                         "platforms": cfg.plan.platforms}}  # fmt: skip

    class _NoTools:
        def offered(self, server: str) -> list[str]:
            return []

    log = RunLog(folder / "analyst", "analyst")
    runner = ClaudeRunner(log, _NoTools(), {}, cfg.budgets.max_turns, cfg.budgets.agent_timeout_s)  # type: ignore[arg-type]
    result = runner.run("analyst", "analyst", json.dumps(task, ensure_ascii=False))
    if result.answer is None:
        typer.echo(f"the analyst failed: {result.error}", err=True)
        raise typer.Exit(1)
    a = result.answer
    lines = [f"# What worked ({pb['pull_id']})", "", f"**{a.get('headline', '')}**", ""]
    for title, key in (("What worked", "what_worked"), ("What did not", "what_did_not"),
                       ("Not enough data yet", "not_enough_data")):  # fmt: skip
        lines += [f"## {title}", *[f"- {x}" for x in a.get(key, [])], ""]
    lines += ["## Proposed changes (yours to approve)"]
    for p in a.get("proposals", []):
        lines += [f"- **{p.get('change')}**", f"  - why: {p.get('why')}", f"  - would be wrong if: {p.get('risk')}"]
    (folder / "analyst.md").write_text("\n".join(lines) + "\n")
    typer.echo((folder / "analyst.md").read_text())
    typer.echo(f"({result.tokens:,} tokens)")
