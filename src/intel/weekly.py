"""`intel weekly`: the Sunday run on the Mac (M7). One command, steps in order, always ends with a run report.

    wait for the data platform's weekly refresh -> warehouse pull -> market snapshot (falls back to the latest one)
    -> live ledger download -> train + judge -> package + upload if it passed -> upload the run report

The run report goes through the same upload-only inbox as packages; the VPS turns it into a Telegram message.
A failure at any step still produces (and tries to upload) an `error` report naming the step.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import time
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from intel.pipeline import steps as pipeline_steps
from intel.plugins import ModelPlugin, RunContext, discover
from intel.settings import Settings, required
from intel.stopping import Stopped, sigterm_stops_cleanly

if TYPE_CHECKING:  # the VPS imports this module (serve/runs.py) but has no pandera: Mac-only training group
    from intel.quality.contracts import Source
    from intel.quality.validate import QualityReport


class StepFailed(RuntimeError):
    def __init__(self, step: str, error: str):
        super().__init__(f"{step}: {error}")
        self.step, self.error = step, error


def mip_refresh_running() -> bool:
    r = subprocess.run(
        ["pgrep", "-f", "MarketIntelligence Data Platform/scripts/weekly_refresh.sh"], capture_output=True, check=False
    )
    return r.returncode == 0


def wait_for_mip(max_wait_s: int, log: Callable[[str], None], poll_s: int = 300) -> int:
    waited = 0
    while mip_refresh_running():
        if waited >= max_wait_s:
            raise RuntimeError(f"data platform weekly refresh still running after {max_wait_s // 3600} h")
        if waited == 0:
            log("data platform weekly refresh is running; waiting for it to finish")
        time.sleep(poll_s)
        waited += poll_s
    return waited


def fetch_ledger(api_url: str, out_dir: Path, client: httpx.Client | None = None) -> tuple[list[dict[str, Any]], Path]:
    http = client or httpx.Client(timeout=60)
    r = http.get(f"{api_url.rstrip('/')}/v1/ledger/export")
    r.raise_for_status()
    rows = r.json()["rows"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}.json"
    data = json.dumps(rows, indent=1, default=str).encode()
    path.write_bytes(data)
    path.with_suffix(".sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  {path.name}\n")
    return rows, path


RETRY_AFTER = dt.timedelta(hours=6)


class Progress:
    """Step-by-step state of the current run: written to data/weekly/current.json and (best effort) uploaded as
    weekly-<run_id>.status.json so the VPS Training page and Telegram /training can show it live."""

    def __init__(
        self,
        run_id: str,
        out_dir: Path,
        uploader: Callable[[Path], None] | None = None,
        steps: dict[str, tuple[str, str]] | None = None,
        only: Sequence[str] | None = None,
    ):
        self.path, self.uploader = out_dir / "current.json", uploader
        self.upload_error: str | None = None
        self.remote = out_dir / f"weekly-{run_id}.status.json"
        now = _now()
        self.state: dict[str, Any] = {
            "kind": "weekly_status",
            "run_id": run_id,
            "state": "running",
            "started_at": now,
            "updated_at": now,
            "seq": 0,
            "current_step": None,
            "steps": {k: {"label": v[0], "status": "pending"} for k, v in (steps or pipeline_steps()).items()},
        }
        if only:  # a single-model run: the page shows the other models' steps as not part of it
            self.state["only"] = list(only)
        out_dir.mkdir(parents=True, exist_ok=True)

    def _save(self) -> None:
        self.state["seq"] += 1
        self.state["updated_at"] = _now()
        data = json.dumps(self.state, indent=1)
        self.path.write_text(data)
        self.remote.write_text(data)
        if self.uploader:
            try:
                self.uploader(self.remote)
            except Exception as e:  # progress is nice-to-have; never fail the run for it
                self.upload_error = f"{type(e).__name__}: {e}"

    def start(self, step: str) -> None:
        self.state["steps"][step].update(status="running", started_at=_now())
        self.state["current_step"] = step
        self._save()

    def done(self, step: str, note: str | None = None, details: dict[str, Any] | None = None) -> None:
        self.state["steps"][step].update(status="done", finished_at=_now(), note=note, details=details or {})
        self._save()

    def skip(self, step: str, note: str) -> None:
        self.state["steps"][step].update(status="skipped", note=note)

    def fail(self, step: str, error: str) -> None:
        self.state["steps"][step].update(status="failed", finished_at=_now(), note=error[:300])
        self._save()

    def finish(self, status: str) -> None:
        for v in self.state["steps"].values():
            if v["status"] == "pending":
                v["status"] = "skipped"
        self.state.update(state="finished", result=status, current_step=None)
        self._save()


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def last_sunday_noon(now: dt.datetime) -> dt.datetime:
    """Most recent Sunday 12:00 Europe/Berlin at or before `now` (the weekly anchor)."""
    from zoneinfo import ZoneInfo

    local = now.astimezone(ZoneInfo("Europe/Berlin"))
    anchor = (local - dt.timedelta(days=local.isoweekday() % 7)).replace(hour=12, minute=0, second=0, microsecond=0)
    if anchor > local:
        anchor -= dt.timedelta(days=7)
    return anchor.astimezone(dt.UTC)


def run_history(weekly_dir: Path) -> list[dict[str, Any]]:
    out = []
    for f in sorted(weekly_dir.glob("weekly-*.run.json")):
        try:
            out.append(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
    return out


def is_due(weekly_dir: Path, now: dt.datetime | None = None) -> tuple[bool, str]:
    """Due when no successful run (pass / fail / baseline) happened since the last Sunday 12:00 Berlin, and the last
    attempt was before that anchor or failed more than RETRY_AFTER ago (so a broken step does not loop hourly)."""
    now = now or dt.datetime.now(dt.UTC)
    anchor = last_sunday_noon(now)
    hist = [
        r for r in run_history(weekly_dir) if not r.get("dry_run") and not r.get("only")
    ]  # not --no-upload, not 1 model
    ok = [
        dt.datetime.fromisoformat(r["finished_at"])
        for r in hist
        if r.get("status") not in ("error", "stopped") and r.get("finished_at")  # a stopped run is an attempt, not done
    ]
    tries = [dt.datetime.fromisoformat(r["started_at"]) for r in hist if r.get("started_at")]
    if ok and max(ok) >= anchor:
        return False, f"done this week ({max(ok):%a %d %b %H:%M} UTC)"
    if tries and max(tries) >= anchor and now - max(tries) < RETRY_AFTER:
        return (
            False,
            f"last attempt did not finish at {max(tries):%H:%M} UTC; retrying after {RETRY_AFTER.seconds // 3600} h",
        )
    return True, f"due since {anchor:%a %d %b %H:%M} UTC"


class Lock:
    """Directory lock with the owner's pid; a lock whose process is gone is stale and taken over."""

    def __init__(self, weekly_dir: Path):
        self.dir = weekly_dir / ".lock"

    def __enter__(self) -> Lock:
        import os

        self.dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.dir.mkdir()
        except FileExistsError:
            try:
                pid = int((self.dir / "pid").read_text())
                os.kill(pid, 0)
                raise RuntimeError(f"another weekly run is in progress (pid {pid})") from None
            except (ProcessLookupError, ValueError, FileNotFoundError):
                pass  # stale: take it over
        (self.dir / "pid").write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc: object) -> None:
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)


def select_plugins(
    only: Sequence[str] | None, plugins: Sequence[ModelPlugin] | None = None
) -> Sequence[ModelPlugin] | None:
    """The plug-ins a run trains: all of them, or those named by step key or model name (`only`)."""
    if not only:
        return plugins
    pool = discover() if plugins is None else plugins
    wanted = set(only)
    chosen = [p for p in pool if p.step in wanted or wanted & set(p.models)]
    known = {x for p in pool for x in (p.step, *p.models)}
    if unknown := wanted - known:
        raise ValueError(f"unknown model or step: {', '.join(sorted(unknown))} (known: {', '.join(sorted(known))})")
    return chosen


SCORERS = ("content_scorer", "reach_scorer")
DRIFT_COOLDOWN = dt.timedelta(days=3)  # early retraining at most this often, however loud the drift


def find_run(runs_dir: Path, version: str) -> Path | None:
    """The training run folder that produced a model version (scorer runs record their version in report.json)."""
    for rd in sorted(runs_dir.glob("*_scorer*"), reverse=True):
        try:
            if json.loads((rd / "report.json").read_text()).get("version") == version:
                return rd
        except (OSError, ValueError):
            continue
    return None


def skew_check(ledger: list[dict[str, Any]], pull: Path, runs_dir: Path) -> dict[str, Any]:
    """Training/serving skew (ADR 0018): rebuild the features of already-scored posts from this pull, with the SAME
    model version's bundle (frozen market context), and compare them with the features logged when serving.
    Any difference means the two code paths (or their inputs) diverged."""
    import joblib

    from intel.data import load_table
    from intel.models.content_scorer.features import MarketContext, build_features

    rows = [r for r in ledger if r.get("features") and r.get("model_name") in SCORERS]
    if not rows:
        return {"checked": 0, "mismatches": 0, "examples": []}
    posts = load_table(pull, "social_post", ["id", "platform", "content_type", "caption", "posted_at"]).set_index("id")
    checked, mismatches, skipped = 0, 0, 0
    examples: list[str] = []
    bundles: dict[str, dict[str, Any] | None] = {}
    for r in rows:
        v = r["model_version"]
        if v not in bundles:
            rd = find_run(runs_dir, v)
            bundles[v] = joblib.load(rd / "model.joblib") if rd else None
        b, pid = bundles[v], str(r["post_id"])
        if b is None or pid not in posts.index:
            skipped += 1
            continue
        row = posts.loc[[pid], ["platform", "content_type", "caption", "posted_at"]].reset_index(drop=True)
        X = build_features(row, MarketContext.from_dict(b["context"]))[b["columns"]].iloc[0]
        checked += 1
        diff = [
            f"{c}: served {r['features'].get(c)!r}, rebuilt {X[c]!r}"
            for c in b["columns"]
            if not _same(r["features"].get(c), X[c])
        ]
        if diff:
            mismatches += 1
            if len(examples) < 3:
                examples.append(f"{pid[:8]} {v}: " + "; ".join(diff)[:200])
    return {"checked": checked, "mismatches": mismatches, "skipped": skipped, "examples": examples}


def _same(served: Any, rebuilt: Any) -> bool:
    import pandas as pd

    if served is None or (isinstance(served, float) and served != served):
        return bool(pd.isna(rebuilt))
    if isinstance(served, bool) or isinstance(rebuilt, bool):
        return bool(served) == bool(rebuilt)
    if isinstance(served, int | float) and not isinstance(served, bool):
        try:
            return abs(float(served) - float(rebuilt)) < 1e-9
        except (TypeError, ValueError):
            return False
    return str(served) == str(rebuilt)


def active_by_model(api_url: str | None, client: httpx.Client | None = None) -> dict[str, str]:
    """Scorer name -> version the VPS has loaded right now (its /ready); empty when unreachable."""
    if not api_url:
        return {}
    try:
        models = (client or httpx.Client(timeout=20)).get(f"{api_url.rstrip('/')}/ready").json().get("models", {})
    except (httpx.HTTPError, ValueError):
        return {}
    return {str(n): str(v) for n, v in models.items() if n in SCORERS}


def active_versions(api_url: str | None, client: httpx.Client | None = None) -> set[str]:
    """Scorer versions the VPS has loaded right now (its /ready); empty when unreachable."""
    return set(active_by_model(api_url, client).values())


def hold_if_not_better(
    passed: list[tuple[Path, dict[str, Any]]],
    active: dict[str, str],
    runs_dir: Path,
    pull: Path,
    log: Callable[[str], None],
) -> tuple[list[tuple[Path, dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Drop passing scorers that don't clearly beat the ACTIVE version (intel.incumbent); returns (still passing,
    comparisons by model). Without a known active version, or its run on this Mac, the owner is asked as before."""
    import yaml

    from intel.incumbent import compare

    keep, comparisons = [], {}
    for rd, rr in passed:
        name = rr["kind"].removesuffix("_run")
        current = active.get(name)
        act_run = find_run(runs_dir, current) if current and current != rr["version"] else None
        if name not in SCORERS or act_run is None:
            keep.append((rd, rr))
            continue
        spec = yaml.safe_load((Path(__file__).parent / "models" / name / "spec.yaml").read_text())
        c = compare(rd, act_run, pull, float(spec["gates"]["min_gain_balanced_accuracy"]))
        comparisons[name] = c.as_dict()
        log(f"{name}: {c.reason}")
        if c.clearly_better:
            keep.append((rd, rr))
    return keep, comparisons


def references_to_send(
    ledger: list[dict[str, Any]] | None,
    new_runs: list[Path],
    d: Path,
    log: Callable[[str], None],
    active: set[str] | None = None,
) -> list[Path]:
    """Reference profiles the VPS should have: this run's scorer versions, the versions it has loaded now (`active`,
    from /ready) and the versions in its ledger, each once. Missing profiles are rebuilt from the saved runs."""
    from intel.monitoring.reference import write_reference

    sent_file = d / "monitoring" / "sent.json"
    sent: set[str] = set(json.loads(sent_file.read_text())) if sent_file.exists() else set()
    serving = (active or set()) | {r["model_version"] for r in (ledger or []) if r.get("model_name") in SCORERS}
    runs = [*new_runs, *(rd for v in sorted(serving) if (rd := find_run(d / "runs", v)) is not None)]
    out, seen = [], set()
    for rd in runs:
        rep = json.loads((rd / "report.json").read_text())
        name, version = rep["kind"].removesuffix("_run"), rep["version"]
        if version in sent or version in seen:
            continue
        seen.add(version)
        ref = rd / "reference.json"
        if not ref.exists():
            pull, market = d / "pulls" / rep["pull_id"], d / "market" / rep["market_id"]
            if not (pull.exists() and market.exists()):
                log(f"no reference for {version}: its snapshots are gone")
                continue
            write_reference(rd, pull, market)
        dest = d / "monitoring" / f"reference-{name}-{version}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(ref.read_bytes())
        out.append(dest)
    return out


def mark_sent(d: Path, files: list[Path]) -> None:
    sent_file = d / "monitoring" / "sent.json"
    sent: set[str] = set(json.loads(sent_file.read_text())) if sent_file.exists() else set()
    sent |= {json.loads(f.read_text())["model_version"] for f in files}
    sent_file.write_text(json.dumps(sorted(sent)))


def drift_due(
    api_url: str | None, weekly_dir: Path, now: dt.datetime | None = None, client: httpx.Client | None = None
) -> tuple[bool, str]:
    """Train early when the VPS's monitoring asks for it (ADR 0018), at most once per DRIFT_COOLDOWN."""
    if not api_url:
        return False, "no API URL: drift trigger off"
    now = now or dt.datetime.now(dt.UTC)
    try:
        mon = (client or httpx.Client(timeout=20)).get(f"{api_url.rstrip('/')}/v1/monitoring").json()
    except (httpx.HTTPError, ValueError) as e:
        return False, f"monitoring not reachable ({type(e).__name__})"
    if not mon.get("retrain_requested"):
        return False, "monitoring: no retraining requested"
    hist = [r for r in run_history(weekly_dir) if not r.get("dry_run") and r.get("finished_at")]
    last = max((dt.datetime.fromisoformat(r["finished_at"]) for r in hist), default=None)
    if last and now - last < DRIFT_COOLDOWN:
        return False, f"monitoring asks for retraining, but the last run was {last:%a %d %b} (cool-down 3 days)"
    return True, "monitoring asks for retraining: " + "; ".join(mon.get("reasons", []))[:200]


def quality_gate(snapshot: Path, source: Source, root: Path, quality_dir: Path) -> QualityReport:
    """Validate a snapshot against its contracts (ADR 0017), comparing volumes with the snapshot before it."""
    from intel.pull import list_pulls
    from intel.quality.validate import validate_snapshot

    earlier = [p for p in list_pulls(root) if p.name < snapshot.name]
    q = validate_snapshot(snapshot, source, earlier[-1] if earlier else None)
    q.save(quality_dir)
    return q


def quality_details(q: QualityReport) -> dict[str, Any]:
    """What the progress page shows about a snapshot's data checks."""
    out: dict[str, Any] = {
        "data checks": f"{len(q.card)} tables: {len(q.errors)} errors, {len(q.warnings)} warnings",
    }
    for i, f in enumerate([*q.errors, *q.warnings][:5]):
        out[f"{f.severity} {i + 1}"] = f.line()[:160]
    return out


def quality_error(q: QualityReport) -> str:
    lines = [f.line() for f in q.errors]
    more = f" (+{len(lines) - 3} more, see data/quality/{q.source}-{q.snapshot_id}.json)" if len(lines) > 3 else ""
    return f"data checks failed for {q.source} {q.snapshot_id}: " + "; ".join(lines[:3]) + more


def run_weekly(
    s: Settings,
    *,
    upload: bool = True,
    wait_max_s: int = 8 * 3600,
    log: Callable[[str], None] = print,
    plugins: Sequence[ModelPlugin] | None = None,
    only: Sequence[str] | None = None,
) -> dict[str, Any]:
    """The Sunday run. `only` (model names or step keys) trains just those models: the same data steps and judge, no
    knowledge pack, and the run does not count as this week's training."""
    chosen = select_plugins(only, plugins)
    with Lock(s.intel_data_dir / "weekly"), sigterm_stops_cleanly():
        return _run_weekly(s, upload=upload, wait_max_s=wait_max_s, log=log, plugins=chosen, only=only)


def _run_weekly(
    s: Settings,
    *,
    upload: bool = True,
    wait_max_s: int = 8 * 3600,
    log: Callable[[str], None] = print,
    plugins: Sequence[ModelPlugin] | None = None,
    only: Sequence[str] | None = None,
) -> dict[str, Any]:
    from intel.market import run_market_pull
    from intel.package import build_package, verify_package
    from intel.package import upload as upload_file
    from intel.pull import list_pulls, run_pull, verify_pull
    from intel.tunnel import warehouse_tunnel

    plugins = discover() if plugins is None else plugins
    started = dt.datetime.now(dt.UTC)
    rep: dict[str, Any] = {
        "kind": "weekly_run",
        "format_version": 1,
        "run_id": f"{started:%Y%m%dT%H%M%SZ}",
        "started_at": started.isoformat(timespec="seconds"),
        "status": "error",
        "failed_step": None,
        "error": None,
        "market_fallback": False,
        "failed_gates": [],
        "dry_run": not upload,
    }
    if only:
        rep["only"] = [p.step for p in plugins]  # a one-model run is not this week's training (see is_due)
    d = s.intel_data_dir
    uploader = (lambda f: _upload_report(f, s)) if upload and s.dk_inbox_ssh and s.dk_inbox_ssh_key else None
    prog = Progress(rep["run_id"], d / "weekly", uploader, pipeline_steps(plugins), only=rep.get("only"))
    stopped = False
    step = "wait"
    results: list[tuple[Path, dict[str, Any]]] = []  # (run folder, report) of every model this run produced
    ledger: list[dict[str, Any]] | None = None
    try:
        prog.start("wait")
        waited = wait_for_mip(wait_max_s, log)
        prog.done(
            "wait",
            f"waited {waited // 60} min" if waited else "data refresh not running",
            {"waited_minutes": waited // 60},
        )

        step = "pull"
        prog.start("pull")
        log("pulling the warehouse")
        with warehouse_tunnel(s.dk_warehouse_ssh, s.dk_warehouse_ssh_key, s.warehouse_local_port):
            pull = run_pull(
                required(s.dk_warehouse_dsn, "DK_WAREHOUSE_DSN"),
                d / "pulls",
                keep=s.intel_keep_pulls,
                log=lambda *_: None,
            )
        if verify_pull(pull):
            raise RuntimeError("pull verification failed")
        rep["pull_id"] = pull.name
        wq = quality_gate(pull, "warehouse", d / "pulls", d / "quality")
        rep["data_quality"] = {"warehouse": wq.summary()}
        if not wq.ok:  # never train on data that breaks a contract the models rely on
            raise RuntimeError(quality_error(wq))
        m = json.loads((pull / "manifest.json").read_text())
        t = m["tables"]
        prog.done(
            "pull",
            f"{len(t)} tables, {m['total_rows']:,} rows",
            {
                "source": "VPS warehouse (read-only role, SSH tunnel over Tailscale)",
                "snapshot": pull.name,
                "tables": len(t),
                "rows": m["total_rows"],
                "posts": t.get("social_post", {}).get("rows"),
                "metric snapshots": t.get("social_metrics_snapshot", {}).get("rows"),
                "website events": t.get("raw_posthog_events", {}).get("rows"),
                "orders": t.get("orders", {}).get("rows"),
                "checksums": "verified",
                **quality_details(wq),
            },
        )

        step = "market"
        prog.start("market")
        try:
            market = run_market_pull(
                required(s.mip_reader_dsn, "MIP_READER_DSN"),
                s.mip_dir,
                d / "market",
                s.intel_keep_pulls,
                log=lambda *_: None,
            )
        except Exception as e:  # market data changes slowly: last week's is acceptable
            existing = list_pulls(d / "market")
            if not existing:
                raise
            market = existing[-1]
            rep["market_fallback"] = True
            log(f"market snapshot failed ({type(e).__name__}); using {market.name}")
        mq = quality_gate(market, "market", d / "market", d / "quality")
        if not mq.ok:  # like an unreachable platform: fall back to the newest earlier snapshot that passes
            log(quality_error(mq))
            for older in reversed([p for p in list_pulls(d / "market") if p.name < market.name]):
                candidate = quality_gate(older, "market", d / "market", d / "quality")
                if candidate.ok:
                    market, mq, rep["market_fallback"] = older, candidate, True
                    break
            else:
                raise RuntimeError(quality_error(mq))
        rep.setdefault("data_quality", {})["market"] = mq.summary()
        rep["market_id"] = market.name
        mm = json.loads((market / "manifest.json").read_text())
        prog.done(
            "market",
            market.name + (" (older snapshot)" if rep["market_fallback"] else ""),
            {
                "source": "Market data platform on this Mac (read-only role)",
                "snapshot": market.name,
                "performance rules": mm["tables"].get("content_feature_performance", {}).get("rows"),
                "occasion days": mm["tables"].get("occasion_day", {}).get("rows"),
                "fallback to older snapshot": rep["market_fallback"],
                **quality_details(mq),
            },
        )

        step = "ledger"
        prog.start("ledger")
        if s.intel_api_url:
            ledger, _ = fetch_ledger(s.intel_api_url, d / "ledger")
            log(f"ledger: {len(ledger)} predictions")
        rows = ledger or []
        skew = skew_check(rows, pull, d / "runs")
        rep["skew"] = skew
        with7 = sum(1 for x in rows if ((x.get("outcomes") or {}).get("7d") or {}).get("label") is not None)
        prog.done(
            "ledger",
            f"{len(rows)} predictions, {with7} with 7-day results",
            {
                "source": "VPS intel-api over Tailscale",
                "predictions": len(rows),
                "with 7-day results": with7,
                "posts": len({x["post_id"] for x in rows}),
                "training/serving skew": f"{skew['mismatches']} of {skew['checked']} served posts differ"
                + (f" (e.g. {skew['examples'][0]})" if skew["examples"] else ""),
            },
        )

        ctx = RunContext(
            pull=pull,
            market=market,
            ledger=ledger,
            runs_dir=d / "runs",
            checks_dir=d / "checks",
            mlflow_uri=f"sqlite:///{(d / 'mlflow.db').resolve()}",
            log=log,
        )
        rep["models"] = []
        for plugin in plugins:
            step = plugin.step
            prog.start(step)
            outcome = plugin.run(ctx)
            if not rep.get("version"):
                rep.update(outcome.headline)  # the first scorer's numbers are the run report's headline
            for run_dir in outcome.run_dirs:
                rr = json.loads((run_dir / "report.json").read_text())
                results.append((run_dir, rr))
                new, current = plugin.scores(rr)
                rep["models"].append(
                    {
                        "name": rr["kind"].removesuffix("_run"),
                        "version": rr["version"],
                        "verdict": rr["judge"]["verdict"],
                        "new": new,
                        "current": current,
                        "failed_gates": rr["judge"].get("failed", []),
                    }
                )
            prog.done(step, outcome.note, outcome.details)

        step = "judge"
        prog.start("judge")
        icon = {"pass": "✓", "fail": "✗", "pending": "⏳"}
        judge_details: dict[str, Any] = {}
        for _, rr in results:
            name, jj = rr["kind"].removesuffix("_run"), rr["judge"]
            judge_details[f"{name}: verdict"] = (
                f"{jj['verdict']} (new {rr['challenger']['test']['balanced_accuracy']} "
                f"vs current {rr['champion']['test']['balanced_accuracy']})"
            )
            judge_details.update(
                {f"{name}: {icon.get(g['status'], '?')} {g['gate']}": g["detail"] for g in jj["gates"]}
            )
        passed = [(rd, rr) for rd, rr in results if rr["judge"]["verdict"] == "pass"]
        # The owner is asked about a new scorer only if it clearly beats the ACTIVE version (intel.incumbent).
        passed, comparisons = hold_if_not_better(passed, active_by_model(s.intel_api_url), d / "runs", pull, log)
        if comparisons:
            rep["incumbent"] = comparisons
        for m in rep["models"]:
            c = comparisons.get(m["name"])
            if c is None:
                continue
            judge_details[f"{m['name']}: vs active"] = c["reason"]
            if not c["clearly_better"]:
                m["verdict"] = "kept_active"
                m["failed_gates"] = [f"not clearly better than active {c['active_version']}"]
                if m.get("version") == rep.get("version"):  # the headline scorer: the report says why nothing came
                    rep["failed_gates"] = [*rep.get("failed_gates", []), "not clearly better than the active version"]
        prog.done("judge", ", ".join(f"{m['name']} {m['verdict']}" for m in rep["models"]), judge_details)

        if passed:
            step = "package"
            prog.start("package")
            tgzs = []
            for rd, _ in passed:
                tgz = build_package(rd, d / "packages", Path.cwd())
                if verify_package(tgz):
                    raise RuntimeError(f"package verification failed: {tgz.name}")
                tgzs.append(tgz)
            rep["package"] = ", ".join(t.name for t in tgzs)
            prog.done(
                "package",
                rep["package"],
                {t.name: f"{round(t.stat().st_size / 1024)} KB, checksums verified" for t in tgzs},
            )
            if upload:
                step = "upload"
                prog.start("upload")
                for tgz in tgzs:
                    upload_file(
                        tgz, required(s.dk_inbox_ssh, "DK_INBOX_SSH"), required(s.dk_inbox_ssh_key, "DK_INBOX_SSH_KEY")
                    )
                prog.done(
                    "upload",
                    f"{len(tgzs)} arrived intact",
                    {"target": "VPS inbox (upload-only)", "remote checksum": "match"},
                )
            rep["status"] = "pass"
        else:
            prog.skip("package", "not better: nothing to package")
            prog.skip("upload", "not better: nothing to upload")
            rep["status"] = "fail"
    except Stopped:  # the owner pressed Stop: say so, keep what exists, skip everything that comes after
        stopped = True
        rep.update(status="stopped", failed_step=step, error="stopped by the owner")
        prog.fail(step, "stopped by the owner")
        log(f"stopped by the owner during {step}")
    except Exception as e:
        rep.update(status="error", failed_step=step, error=f"{type(e).__name__}: {e}"[:500])
        prog.fail(step, rep["error"])
        log(traceback.format_exc())
    if stopped:
        prog.skip("knowledge", "stopped by the owner")
    elif only:
        prog.skip("knowledge", "single-model run: the knowledge pack is rebuilt by the full run")
    elif rep.get("pull_id"):  # the knowledge pack: a soft step, it never fails the model run
        prog.start("knowledge")
        try:
            from intel.rag.lifecycle import weekly_refresh

            k = weekly_refresh(d / "pulls" / rep["pull_id"], log)
            rep["knowledge"] = {key: v for key, v in k.items() if key != "gate"} | {
                "gate_passed": (k.get("gate") or {}).get("passed"),
                "gate_lost": (k.get("gate") or {}).get("lost"),
                "gate_reasons": [str(x)[:150] for x in ((k.get("gate") or {}).get("reasons") or [])[:5]],
            }
            if k["status"] == "skipped":
                prog.skip("knowledge", k["why"])
            else:
                prog.done(
                    "knowledge",
                    f"{k['pack']} {'is live' if k['status'] == 'active' else 'refused by its gate: last week stays'}",
                    {"chunks": k.get("chunks"), "newly embedded": k.get("newly_embedded")},
                )
        except Exception as e:
            rep["knowledge"] = {"status": "error", "error": f"{type(e).__name__}: {e}"[:300]}
            prog.fail("knowledge", rep["knowledge"]["error"])
            log(traceback.format_exc())
    else:
        prog.skip("knowledge", "no pull this week")
    if upload and not stopped and s.dk_inbox_ssh and s.dk_inbox_ssh_key and rep.get("pull_id"):
        try:  # reference profiles for the VPS's model monitoring (ADR 0018); never fails the run
            new_runs = [rd for rd, rr in results if rr["kind"].removesuffix("_run") in SCORERS]
            refs = references_to_send(ledger, new_runs, d, log, active_versions(s.intel_api_url))
            for f in refs:
                _upload_report(f, s)
            mark_sent(d, refs)
            rep["references"] = [f.stem.removeprefix("reference-") for f in refs]
        except Exception as e:
            log(f"reference profiles not sent: {type(e).__name__}: {e}")
    rep["finished_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")

    out = d / "weekly" / f"weekly-{rep['run_id']}.run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, default=str) + "\n")
    try:  # the VPS silently drops a report that doesn't fit its schema: say so here, where the owner can see it
        from intel.serve.runs import RunReport

        RunReport.model_validate(json.loads(out.read_text()))
    except Exception as e:  # noqa: BLE001 - a warning, never a failure of the run
        log(f"WARNING: the VPS will REJECT this run report ({type(e).__name__}: {str(e)[:300]}); fix the schema")
        rep["report_invalid"] = True
    if upload and not stopped and s.dk_inbox_ssh and s.dk_inbox_ssh_key:
        prog.start("report")
        try:
            _upload_report(out, s)
            rep["report_uploaded"] = True
            prog.done("report", "sent; Telegram message follows", {"result": rep["status"]})
        except Exception as e:
            rep["report_uploaded"] = False
            prog.fail("report", f"could not upload: {e}")
            log(f"could not upload the run report: {e}")
    else:
        prog.skip("report", "stopped by the owner" if stopped else "dry run: not uploaded")
    prog.finish(rep["status"])
    return rep


def _upload_report(path: Path, s: Settings) -> None:
    from intel.package import rsync_command

    cmd = rsync_command(
        [path], required(s.dk_inbox_ssh, "DK_INBOX_SSH"), required(s.dk_inbox_ssh_key, "DK_INBOX_SSH_KEY")
    )
    subprocess.run(cmd, check=True, capture_output=True)
