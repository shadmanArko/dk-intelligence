"""`intel` command line."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import psycopg
import typer

from intel.pull import list_pulls, list_tables, resolve_pull, run_pull, verify_pull
from intel.settings import get_settings, redact
from intel.tunnel import TunnelError, warehouse_tunnel

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Dhaka Kacchi intelligence")
pulls_app = typer.Typer(no_args_is_help=True, help="Inspect warehouse pulls")
app.add_typer(pulls_app, name="pulls")
market_app = typer.Typer(no_args_is_help=True, help="Market knowledge snapshots from the data platform")
app.add_typer(market_app, name="market")
train_app = typer.Typer(no_args_is_help=True, help="Train models and judge them against the champion")
app.add_typer(train_app, name="train")
package_app = typer.Typer(no_args_is_help=True, help="Seal runs into model packages and upload them (door 2)")
app.add_typer(package_app, name="package")
champion_app = typer.Typer(no_args_is_help=True, help="The current champion model")
app.add_typer(champion_app, name="champion")

from intel.mcp_server.cli import mcp_app, pack_app  # noqa: E402 (no fastmcp import at module level)

app.add_typer(mcp_app, name="mcp")
app.add_typer(pack_app, name="pack")

from intel.studio.cli import studio_app  # noqa: E402

app.add_typer(studio_app, name="studio")


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
                info = c.execute(
                    "select current_user, current_database(), current_setting('default_transaction_read_only')"
                ).fetchone()
                assert info is not None  # a select without FROM always returns one row
                user, db, ro = info
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


@market_app.command("pull")
def market_pull() -> None:
    """Snapshot market priors, the occasion calendar and the cue dictionary from the data platform."""
    from intel.market import run_market_pull

    s = get_settings()
    if not s.mip_reader_dsn:
        typer.secho("MIP_READER_DSN is not set", fg="red")
        raise typer.Exit(1)
    out = run_market_pull(s.mip_reader_dsn, s.mip_dir, s.intel_data_dir / "market", s.intel_keep_pulls)
    problems = verify_pull(out)
    if problems:
        typer.secho("verification FAILED: " + "; ".join(problems), fg="red")
        raise typer.Exit(1)
    typer.secho(f"market snapshot {out.name} verified -> {out}", fg="green")


@train_app.command("content-scorer")
def train_content_scorer(
    pull_id: str = typer.Option(None, help="Default: latest warehouse pull"),
    market_id: str = typer.Option(None, help="Default: latest market snapshot"),
    mlflow: bool = typer.Option(True, help="Log the run to MLflow (data/mlflow.db)"),
    baseline: bool = typer.Option(False, help="Package the champion's recipe in the new format (no gain claimed)"),
    target: str = typer.Option("engagement", help="engagement (content_scorer) or reach (reach_scorer)"),
) -> None:
    """Train a post scorer (engagement or reach question), judge it against today's predictor on the same split."""
    from intel.models.content_scorer.run import run

    s = get_settings()
    d = s.intel_data_dir
    uri = f"sqlite:///{(d / 'mlflow.db').resolve()}" if mlflow else None
    out = run(
        resolve_pull(d / "pulls", pull_id),
        resolve_pull(d / "market", market_id),
        d / "runs",
        uri,
        baseline=baseline,
        target=target,
    )
    r = json.loads((out / "report.json").read_text())
    j = r["judge"]
    typer.echo(f"challenger {r['version']}: {r['challenger']['model']} {r['challenger']['params']}")
    for g in j["gates"]:
        color = {"pass": "green", "fail": "red", "pending": "yellow"}[g["status"]]
        typer.secho(f"  {g['status']:7s} {g['gate']:18s} {g['detail']}", fg=color)
    ok = j["verdict"] in ("pass", "baseline")
    typer.secho(f"verdict: {j['verdict'].upper()}  -> {out}", fg="green" if ok else "red")


def _latest_run(kind: str) -> Path:
    runs = sorted((get_settings().intel_data_dir / "runs").glob(f"*_{kind}"))
    if not runs:
        raise typer.BadParameter(f"no {kind} runs yet")
    return runs[-1]


@package_app.command("build")
def package_build(
    run: str = typer.Option(None, help="Run folder (default: latest content_scorer run)"),
    allow_fail: bool = typer.Option(False, help="Package a failed run as a pipeline-test (never approvable)"),
) -> None:
    """Seal a training run into data/packages/<name>-<version>.tar.gz (+ .sha256)."""
    from intel.package import build_package, verify_package

    s = get_settings()
    try:
        tgz = build_package(
            Path(run) if run else _latest_run("content_scorer*"), s.intel_data_dir / "packages", Path.cwd(), allow_fail
        )
    except ValueError as e:
        typer.secho(str(e), fg="red")
        raise typer.Exit(1) from e
    problems = verify_package(tgz)
    if problems:
        typer.secho("package FAILED verification: " + "; ".join(problems), fg="red")
        raise typer.Exit(1)
    typer.secho(f"package {tgz.name} ({tgz.stat().st_size / 1024:.0f} KB) verified -> {tgz}", fg="green")


@package_app.command("verify")
def package_verify(tgz: Path) -> None:
    """Check a package's outer and inner checksums and structure."""
    from intel.package import verify_package

    problems = verify_package(tgz)
    if problems:
        typer.secho("; ".join(problems), fg="red")
        raise typer.Exit(1)
    typer.secho(f"{tgz.name}: OK", fg="green")


@package_app.command("upload")
def package_upload(tgz: Path) -> None:
    """Upload a verified package to the VPS inbox (upload-only) and confirm the remote bytes match."""
    from intel.package import upload, verify_package

    s = get_settings()
    if not (s.dk_inbox_ssh and s.dk_inbox_ssh_key):
        typer.secho("DK_INBOX_SSH and DK_INBOX_SSH_KEY must be set (see .env.example)", fg="red")
        raise typer.Exit(1)
    problems = verify_package(tgz)
    if problems:
        typer.secho("refusing to upload: " + "; ".join(problems), fg="red")
        raise typer.Exit(1)
    try:
        upload(tgz, s.dk_inbox_ssh, s.dk_inbox_ssh_key)
    except (subprocess.CalledProcessError, RuntimeError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        typer.secho(f"upload failed: {detail.strip()}", fg="red")
        raise typer.Exit(1) from e
    typer.secho(f"uploaded {tgz.name}; remote checksums match", fg="green")


@app.command()
def weekly(
    upload: bool = typer.Option(True, help="Upload the package (if it passed), progress and the run report to the VPS"),
    wait_hours: int = typer.Option(8, help="Max hours to wait for the data platform's weekly refresh"),
    if_due: bool = typer.Option(
        False, "--if-due", help="Only run if this week's training has not happened yet, or monitoring asks for it"
    ),
) -> None:
    """M7: the Sunday run. Pull, market snapshot, ledger, train + judge, package/upload if better, report."""
    from intel.weekly import drift_due, is_due, run_weekly

    s = get_settings()
    if if_due:
        due, why = is_due(s.intel_data_dir / "weekly")
        if not due:  # the weekly anchor is not due: the VPS's model monitoring may still ask for an early run
            due, drift_why = drift_due(s.intel_api_url, s.intel_data_dir / "weekly")
            why = drift_why if due else f"{why}; {drift_why}"
        if not due:
            typer.echo(f"not due: {why}")
            return
        typer.echo(why)
    try:
        r = run_weekly(s, upload=upload, wait_max_s=wait_hours * 3600)
    except RuntimeError as e:  # lock held by a running run
        typer.secho(str(e), fg="yellow")
        return
    color = {"pass": "green", "fail": "yellow", "baseline": "green"}.get(r["status"], "red")
    where = f" at {r['failed_step']}: {r['error']}" if r["status"] == "error" else ""
    typer.secho(f"weekly run {r['run_id']}: {r['status'].upper()}{where}", fg=color)
    if r.get("challenger") and r.get("champion"):
        typer.echo(
            f"  new {r['challenger']['balanced_accuracy']} vs current {r['champion']['balanced_accuracy']} "
            f"(failed: {', '.join(r['failed_gates']) or 'none'}); live: {r.get('live')}"
        )
    typer.echo(f"  report uploaded: {r.get('report_uploaded', False)}")
    if r["status"] == "error":
        raise typer.Exit(1)


@app.command()
def quality(
    pull_id: str = typer.Option(None, "--pull", help="Warehouse pull id (default: latest)"),
    market_id: str = typer.Option(None, "--market", help="Market snapshot id (default: latest)"),
) -> None:
    """Data checks (ADR 0017): contracts, volumes, freshness and the data card of a pull and a market snapshot."""
    from intel.pull import list_pulls
    from intel.quality.validate import validate_snapshot
    from intel.settings import get_settings

    d = get_settings().intel_data_dir
    failed = False
    for source, root, wanted in (("warehouse", d / "pulls", pull_id), ("market", d / "market", market_id)):
        snaps = list_pulls(root)
        if not snaps:
            typer.echo(f"{source}: no snapshots in {root}")
            continue
        snap = next((p for p in snaps if p.name == wanted), None) if wanted else snaps[-1]
        if snap is None:
            raise typer.BadParameter(f"{source} snapshot {wanted} not found")
        earlier = [p for p in snaps if p.name < snap.name]
        q = validate_snapshot(snap, source, earlier[-1] if earlier else None)  # type: ignore[arg-type]
        path = q.save(d / "quality")
        typer.echo(f"== {source} {snap.name}: {len(q.card)} tables, {len(q.errors)} errors, {len(q.warnings)} warnings")
        for f in q.findings:
            typer.echo(f"   {f.severity.upper():7} {f.line()}")
        for table, card in q.card.items():
            prev = card.get("rows_previous")
            change = f" ({card['rows'] - prev:+,} vs previous)" if prev is not None else ""
            age = f", newest {card['age_hours']} h before the snapshot" if "age_hours" in card else ""
            typer.echo(f"   {table:30} {card.get('rows', 0):>8,} rows{change}{age}")
        typer.echo(f"   card: {path}")
        failed |= not q.ok
    raise typer.Exit(1 if failed else 0)


@app.command("send-references")
def send_references(
    dry_run: bool = typer.Option(False, "--dry-run", help="Build and list the profiles, upload nothing"),
) -> None:
    """Send the reference profiles the VPS's model monitoring needs (ADR 0018) now, instead of on Sunday: the versions
    it is serving (read from the ledger) and any new scorer run, each once."""
    from intel.settings import get_settings, required
    from intel.weekly import _upload_report, active_versions, fetch_ledger, mark_sent, references_to_send

    s = get_settings()
    d = s.intel_data_dir
    api = required(s.intel_api_url, "INTEL_API_URL")
    ledger, _ = fetch_ledger(api, d / "ledger")
    active = active_versions(api)
    typer.echo(f"active on the VPS: {', '.join(sorted(active)) or 'none'}")
    refs = references_to_send(ledger, [], d, typer.echo, active)
    for f in refs:
        typer.echo(f"{'would send' if dry_run else 'sending'} {f.name}")
        if not dry_run:
            _upload_report(f, s)
    if refs and not dry_run:
        mark_sent(d, refs)
    typer.echo(f"{len(refs)} reference profile(s) {'to send' if dry_run else 'sent'}")


@app.command("reproduce")
def reproduce_cmd(run: str = typer.Argument(..., help="Run folder name in data/runs (or a path)")) -> None:
    """Train a scorer run again from its recorded snapshots and compare everything (ADR 0019)."""
    from intel.reproduce import reproduce
    from intel.settings import get_settings

    d = get_settings().intel_data_dir
    run_dir = Path(run) if Path(run).exists() else d / "runs" / run
    r = reproduce(run_dir, d, log=lambda *_: None)
    typer.echo(
        f"run {r['run']} ({r['original']}): pull {r['pull']}, market {r['market']}, sealed code {r['sealed_code']}"
    )
    if r["identical"]:
        typer.secho(f"reproducible: identical model, metrics and all {r['test_posts']} test predictions", fg="green")
        return
    typer.secho("NOT reproducible:", fg="red")
    for d_ in r["differences"]:
        typer.echo(f"  {d_}")
    raise typer.Exit(1)


@app.command("loadtest")
def loadtest_cmd(
    url: str = typer.Argument(..., help="Base URL of intel-api, e.g. http://127.0.0.1:8710 or the tailnet URL"),
    endpoint: str = typer.Option("deep", help="deep | batch (no writes) | predict (writes; local only)"),
    levels: str = typer.Option("1,10,50", help="Concurrency levels"),
    requests: int = typer.Option(200, help="Requests per level"),
) -> None:
    """Throughput and p50/p95/p99 latency per concurrency level (ADR 0023)."""
    from intel.loadtest import run

    for r in run(url, tuple(int(x) for x in levels.split(",")), requests, endpoint):  # type: ignore[arg-type]
        typer.echo(
            f"{r.endpoint:8} c={r.concurrency:<3} {r.rps:>7} req/s  p50 {r.p50_ms:>8} ms  p95 {r.p95_ms:>8} ms"
            f"  p99 {r.p99_ms:>8} ms  errors {r.errors}"
        )


contracts_app = typer.Typer(no_args_is_help=True, help="The contracts between the projects (contracts/registry.yaml)")
app.add_typer(contracts_app, name="contracts")


@contracts_app.command("sync")
def contracts_sync(
    check: bool = typer.Option(False, "--check", help="only report drift (exit 1 if an owner changed a contract)"),
) -> None:
    """Compare the pinned contracts with their owners' current ones (reels files, dk-publishing's /openapi.json)."""
    from intel.contracts import sync

    report = sync(check=check)
    for d in report:
        typer.echo(f"{d.status:12} {d.name}  {d.detail}".rstrip())
    drift = [d for d in report if d.status in ("changed", "new")]
    if check and drift:
        typer.echo(f"{len(drift)} contract(s) changed: run `intel contracts sync`, review the diff, run the tests")
        raise typer.Exit(1)


@app.command("weekly-status")
def weekly_status() -> None:
    """Show the current/last Sunday run step by step, recent runs, and whether this week's run is due."""
    from intel.weekly import is_due, run_history

    w = get_settings().intel_data_dir / "weekly"
    cur = w / "current.json"
    if cur.exists():
        st = json.loads(cur.read_text())
        typer.echo(
            f"run {st['run_id']}: {st['state']}{' (' + st.get('result', '') + ')' if st.get('result') else ''}"
            f", updated {st['updated_at']}"
        )
        icons = {"done": "✓", "running": "⏳", "failed": "✗", "skipped": "–", "pending": "·"}
        for v in st["steps"].values():
            typer.echo(f"  {icons.get(v['status'], '?')} {v['label']:28s} {v.get('note') or ''}")
    due, why = is_due(w)
    typer.echo(f"this week: {'DUE - ' if due else ''}{why}")
    for r in run_history(w)[-5:]:
        typer.echo(f"  {r['run_id']}  {r['status']:8s} {r.get('failed_step') or ''}")


@app.command()
def control(
    port: int = typer.Option(8765, help="Local port (127.0.0.1 only)"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the page in the browser"),
) -> None:
    """Control Room: a local page with a Run button and the live pipeline diagram (this Mac only)."""
    import threading
    import webbrowser

    import uvicorn

    from intel.control import create_control_app

    url = f"http://127.0.0.1:{port}/"
    app_ = create_control_app(get_settings(), Path.cwd(), port)
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    typer.echo(f"Control Room on {url} (Ctrl+C to stop)")
    uvicorn.run(app_, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    app()
