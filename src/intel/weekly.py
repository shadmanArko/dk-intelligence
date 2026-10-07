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
from pathlib import Path

import httpx

from intel.pipeline import STEPS as PIPELINE_STEPS
from intel.settings import Settings

STEPS = tuple(PIPELINE_STEPS)


class StepFailed(RuntimeError):
    def __init__(self, step: str, error: str):
        super().__init__(f"{step}: {error}")
        self.step, self.error = step, error


def mip_refresh_running() -> bool:
    r = subprocess.run(
        ["pgrep", "-f", "MarketIntelligence Data Platform/scripts/weekly_refresh.sh"], capture_output=True, check=False
    )
    return r.returncode == 0


def wait_for_mip(max_wait_s: int, log, poll_s: int = 300) -> int:
    waited = 0
    while mip_refresh_running():
        if waited >= max_wait_s:
            raise RuntimeError(f"data platform weekly refresh still running after {max_wait_s // 3600} h")
        if waited == 0:
            log("data platform weekly refresh is running; waiting for it to finish")
        time.sleep(poll_s)
        waited += poll_s
    return waited


def fetch_ledger(api_url: str, out_dir: Path, client: httpx.Client | None = None) -> tuple[list[dict], Path]:
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


def _scores(t: dict | None) -> dict | None:
    if not t:
        return None
    return {k: t.get(k) for k in ("balanced_accuracy", "roc_auc", "brier", "n")}


STEP_LABELS = {k: v[0] for k, v in PIPELINE_STEPS.items()}
RETRY_AFTER = dt.timedelta(hours=6)


class Progress:
    """Step-by-step state of the current run: written to data/weekly/current.json and (best effort) uploaded as
    weekly-<run_id>.status.json so the VPS Training page and Telegram /training can show it live."""

    def __init__(self, run_id: str, out_dir: Path, uploader=None):
        self.path, self.uploader, self.upload_error = out_dir / "current.json", uploader, None
        self.remote = out_dir / f"weekly-{run_id}.status.json"
        now = _now()
        self.state = {
            "kind": "weekly_status",
            "run_id": run_id,
            "state": "running",
            "started_at": now,
            "updated_at": now,
            "seq": 0,
            "current_step": None,
            "steps": {k: {"label": v, "status": "pending"} for k, v in STEP_LABELS.items()},
        }
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

    def done(self, step: str, note: str | None = None, details: dict | None = None) -> None:
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


def run_history(weekly_dir: Path) -> list[dict]:
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
    hist = [r for r in run_history(weekly_dir) if not r.get("dry_run")]  # --no-upload runs never reached the VPS
    ok = [
        dt.datetime.fromisoformat(r["finished_at"]) for r in hist if r.get("status") != "error" and r.get("finished_at")
    ]
    tries = [dt.datetime.fromisoformat(r["started_at"]) for r in hist if r.get("started_at")]
    if ok and max(ok) >= anchor:
        return False, f"done this week ({max(ok):%a %d %b %H:%M} UTC)"
    if tries and max(tries) >= anchor and now - max(tries) < RETRY_AFTER:
        return False, f"last attempt failed at {max(tries):%H:%M} UTC; retrying after {RETRY_AFTER.seconds // 3600} h"
    return True, f"due since {anchor:%a %d %b %H:%M} UTC"


class Lock:
    """Directory lock with the owner's pid; a lock whose process is gone is stale and taken over."""

    def __init__(self, weekly_dir: Path):
        self.dir = weekly_dir / ".lock"

    def __enter__(self):
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

    def __exit__(self, *exc):
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)


def run_weekly(s: Settings, *, upload: bool = True, wait_max_s: int = 8 * 3600, log=print) -> dict:
    with Lock(s.intel_data_dir / "weekly"):
        return _run_weekly(s, upload=upload, wait_max_s=wait_max_s, log=log)


def _run_weekly(s: Settings, *, upload: bool = True, wait_max_s: int = 8 * 3600, log=print) -> dict:
    from intel.market import run_market_pull
    from intel.models.content_scorer.run import run as train_run
    from intel.package import build_package, verify_package
    from intel.package import upload as upload_file
    from intel.pull import list_pulls, run_pull, verify_pull
    from intel.tunnel import warehouse_tunnel

    started = dt.datetime.now(dt.UTC)
    rep: dict = {
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
    d = s.intel_data_dir
    uploader = (lambda f: _upload_report(f, s)) if upload and s.dk_inbox_ssh and s.dk_inbox_ssh_key else None
    prog = Progress(rep["run_id"], d / "weekly", uploader)
    step = "wait"
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
            pull = run_pull(s.dk_warehouse_dsn, d / "pulls", keep=s.intel_keep_pulls, log=lambda *_: None)
        if verify_pull(pull):
            raise RuntimeError("pull verification failed")
        rep["pull_id"] = pull.name
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
            },
        )

        step = "market"
        prog.start("market")
        try:
            market = run_market_pull(s.mip_reader_dsn, s.mip_dir, d / "market", s.intel_keep_pulls, log=lambda *_: None)
        except Exception as e:  # market data changes slowly: last week's is acceptable
            existing = list_pulls(d / "market")
            if not existing:
                raise
            market = existing[-1]
            rep["market_fallback"] = True
            log(f"market snapshot failed ({type(e).__name__}); using {market.name}")
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
            },
        )

        step = "ledger"
        prog.start("ledger")
        ledger = None
        if s.intel_api_url:
            ledger, _ = fetch_ledger(s.intel_api_url, d / "ledger")
            log(f"ledger: {len(ledger)} predictions")
        rows = ledger or []
        with7 = sum(1 for x in rows if ((x.get("outcomes") or {}).get("7d") or {}).get("label") is not None)
        prog.done(
            "ledger",
            f"{len(rows)} predictions, {with7} with 7-day results",
            {
                "source": "VPS intel-api over Tailscale",
                "predictions": len(rows),
                "with 7-day results": with7,
                "posts": len({x["post_id"] for x in rows}),
            },
        )

        step = "train"
        prog.start("train")
        log("training and judging")
        mlflow_uri = f"sqlite:///{(d / 'mlflow.db').resolve()}"
        run_dir = train_run(pull, market, d / "runs", mlflow_uri, log=log, ledger=ledger)
        r = json.loads((run_dir / "report.json").read_text())
        j = r["judge"]
        rep.update(
            version=r["version"],
            posts=r["split"]["train"] + r["split"]["test"],
            challenger=_scores(r["challenger"]["test"]),
            champion=_scores(r["champion"]["test"]),
            gain=j.get("gain"),
            failed_gates=j.get("failed", []),
            live=next((g["detail"] for g in j["gates"] if g["gate"] == "live_evidence"), None),
        )
        ch = r["challenger"]
        prog.done(
            "train",
            f"best: {ch['model']}",
            {
                "training posts": r["split"]["train"],
                "test posts (newest, unseen)": r["split"]["test"],
                "test period from": r["split"]["cutoff"][:10],
                "candidates (CV score)": ", ".join(
                    f"{c['name']} {c['cv_balanced_accuracy@0.5']}" for c in r.get("leaderboard_cv", [])
                )
                or "–",
                "chosen": f"{ch['model']} {ch['params']}",
                "features": ch["features"],
                "threshold": ch["threshold"],
            },
        )

        step = "judge"
        prog.start("judge")
        icon = {"pass": "✓", "fail": "✗", "pending": "⏳"}
        prog.done(
            "judge",
            f"{j['verdict']}: new {rep['challenger']['balanced_accuracy']} vs current "
            f"{rep['champion']['balanced_accuracy']}",
            {
                "verdict": j["verdict"],
                "new model (balanced accuracy)": rep["challenger"]["balanced_accuracy"],
                "current model": rep["champion"]["balanced_accuracy"],
                "gain": j.get("gain"),
                **{f"{icon.get(g['status'], '?')} {g['gate']}": g["detail"] for g in j["gates"]},
            },
        )

        if j["verdict"] == "pass":
            step = "package"
            prog.start("package")
            tgz = build_package(run_dir, d / "packages", Path.cwd())
            if verify_package(tgz):
                raise RuntimeError("package verification failed")
            rep["package"] = tgz.name
            prog.done(
                "package",
                tgz.name,
                {"file": tgz.name, "size KB": round(tgz.stat().st_size / 1024), "checksums": "verified"},
            )
            if upload:
                step = "upload"
                prog.start("upload")
                upload_file(tgz, s.dk_inbox_ssh, s.dk_inbox_ssh_key)
                prog.done("upload", "arrived intact", {"target": "VPS inbox (upload-only)", "remote checksum": "match"})
            rep["status"] = "pass"
        else:
            prog.skip("package", "not better: nothing to package")
            prog.skip("upload", "not better: nothing to upload")
            rep["status"] = "fail"
    except Exception as e:
        rep.update(status="error", failed_step=step, error=f"{type(e).__name__}: {e}"[:500])
        prog.fail(step, rep["error"])
        log(traceback.format_exc())
    rep["finished_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")

    out = d / "weekly" / f"weekly-{rep['run_id']}.run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, default=str) + "\n")
    if upload and s.dk_inbox_ssh and s.dk_inbox_ssh_key:
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
        prog.skip("report", "dry run: not uploaded")
    prog.finish(rep["status"])
    return rep


def _upload_report(path: Path, s: Settings) -> None:
    from intel.package import rsync_command

    subprocess.run(rsync_command([path], s.dk_inbox_ssh, s.dk_inbox_ssh_key), check=True, capture_output=True)
