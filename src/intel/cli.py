"""`intel` command line."""

from __future__ import annotations

import json

import psycopg
import typer

from intel.pull import list_pulls, list_tables, resolve_pull, run_pull, verify_pull
from intel.settings import get_settings, redact
from intel.tunnel import TunnelError, warehouse_tunnel

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Dhaka Kacchi intelligence")
pulls_app = typer.Typer(no_args_is_help=True, help="Inspect warehouse pulls")
app.add_typer(pulls_app, name="pulls")
champion_app = typer.Typer(no_args_is_help=True, help="The current champion model")
app.add_typer(champion_app, name="champion")


def _pulls_dir():
    return get_settings().intel_data_dir / "pulls"


@app.command()
def doctor() -> None:
    """Check settings, the tunnel and the warehouse login (read-only), without printing secrets."""
    s = get_settings()
    typer.echo(f"warehouse dsn : {redact(s.dk_warehouse_dsn)}")
    typer.echo(
        f"ssh           : {s.dk_warehouse_ssh or '<unset>'}  key exists: "
        f"{bool(s.dk_warehouse_ssh_key and s.dk_warehouse_ssh_key.exists())}"
    )
    if not s.dk_warehouse_dsn:
        raise typer.Exit(1)
    try:
        with warehouse_tunnel(s.dk_warehouse_ssh, s.dk_warehouse_ssh_key, s.warehouse_local_port) as opened:
            typer.echo(
                f"tunnel        : {'opened' if opened else 'reused existing listener'} "
                f"on localhost:{s.warehouse_local_port}"
            )
            with psycopg.connect(s.dk_warehouse_dsn, connect_timeout=15) as c:
                user, db, ro = c.execute(
                    "select current_user, current_database(), current_setting('default_transaction_read_only')"
                ).fetchone()
                n = len(list_tables(c))
            typer.echo(f"login         : {user}@{db}, read-only={ro}, readable tables={n}")
            if ro != "on":
                typer.secho("WARNING: role is not read-only by default", fg="yellow")
    except (TunnelError, psycopg.OperationalError) as e:
        typer.secho(f"FAILED: {e}", fg="red")
        raise typer.Exit(1) from e
    typer.secho("OK", fg="green")


@app.command()
def pull(
    tables: str = typer.Option("", help="Comma-separated tables (default: every readable table)"),
    keep: int = typer.Option(None, help="Pulls to keep (default INTEL_KEEP_PULLS)"),
) -> None:
    """Snapshot the VPS warehouse into data/pulls/<id>/ (Parquet + manifest + checksums)."""
    s = get_settings()
    if not s.dk_warehouse_dsn:
        typer.secho("DK_WAREHOUSE_DSN is not set (see .env.example)", fg="red")
        raise typer.Exit(1)
    wanted = [t.strip() for t in tables.split(",") if t.strip()] or None
    try:
        with warehouse_tunnel(s.dk_warehouse_ssh, s.dk_warehouse_ssh_key, s.warehouse_local_port):
            out = run_pull(s.dk_warehouse_dsn, _pulls_dir(), wanted, keep or s.intel_keep_pulls)
    except (TunnelError, psycopg.OperationalError) as e:
        typer.secho(f"pull failed: {e}", fg="red")
        raise typer.Exit(1) from e
    problems = verify_pull(out)
    if problems:
        typer.secho("verification FAILED: " + "; ".join(problems), fg="red")
        raise typer.Exit(1)
    m = json.loads((out / "manifest.json").read_text())
    typer.secho(
        f"pull {m['pull_id']}: {len(m['tables'])} tables, {m['total_rows']:,} rows, verified -> {out}", fg="green"
    )


@pulls_app.command("list")
def pulls_list() -> None:
    """List complete pulls."""
    for p in list_pulls(_pulls_dir()):
        m = json.loads((p / "manifest.json").read_text())
        typer.echo(
            f"{p.name}  snapshot {m['snapshot_at'][:19]}  {len(m['tables']):>3} tables  {m['total_rows']:>9,} rows"
        )


@pulls_app.command("verify")
def pulls_verify(pull_id: str = typer.Argument(None, help="Default: latest")) -> None:
    """Re-check checksums and row counts of a pull."""
    p = resolve_pull(_pulls_dir(), pull_id)
    problems = verify_pull(p)
    if problems:
        typer.secho(f"{p.name}: " + "; ".join(problems), fg="red")
        raise typer.Exit(1)
    typer.secho(f"{p.name}: OK", fg="green")


@champion_app.command("reproduce")
def champion_reproduce(pull_id: str = typer.Option(None, help="Default: latest pull")) -> None:
    """M2: reproduce the harness predictor and write the benchmark the challenger must beat."""
    from intel.champions.reproduce import run

    s = get_settings()
    out = run(resolve_pull(_pulls_dir(), pull_id), s.harness_dir, s.intel_data_dir / "runs")
    r = json.loads((out / "report.json").read_text())
    a, b, c = r["A_recipe_check"], r["B_production_on_unseen_posts"], r["C_benchmark_for_challenger"]
    typer.echo(
        f"A recipe check      : {a['balanced_accuracy']} (published {a['published_balanced_accuracy']}) "
        f"exact={a['reproduced_exactly']}"
    )
    bs = b.get("score", {})
    typer.echo(
        f"B production unseen : n={b['unseen_matured_posts']} balanced_acc={bs.get('balanced_accuracy')} "
        f"ci95={bs.get('balanced_accuracy_ci95')} auc={bs.get('roc_auc')}"
    )
    cs = c["score"]
    typer.echo(
        f"C benchmark         : train={c['train']} test={c['test']} balanced_acc={cs['balanced_accuracy']} "
        f"ci95={cs['balanced_accuracy_ci95']} auc={cs['roc_auc']} brier={cs['brier']}"
    )
    typer.echo(f"report -> {out}")


if __name__ == "__main__":
    app()
