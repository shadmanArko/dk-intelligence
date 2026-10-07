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

from intel.settings import Settings

STEPS = ("wait", "pull", "market", "ledger", "train", "package", "upload")


class StepFailed(RuntimeError):
    def __init__(self, step: str, error: str):
        super().__init__(f"{step}: {error}")
        self.step, self.error = step, error


def mip_refresh_running() -> bool:
    r = subprocess.run(
        ["pgrep", "-f", "MarketIntelligence Data Platform/scripts/weekly_refresh.sh"], capture_output=True, check=False
    )
    return r.returncode == 0


def wait_for_mip(max_wait_s: int, log, poll_s: int = 300) -> None:
    waited = 0
    while mip_refresh_running():
        if waited >= max_wait_s:
            raise RuntimeError(f"data platform weekly refresh still running after {max_wait_s // 3600} h")
        if waited == 0:
            log("data platform weekly refresh is running; waiting for it to finish")
        time.sleep(poll_s)
        waited += poll_s


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


def run_weekly(s: Settings, *, upload: bool = True, wait_max_s: int = 8 * 3600, log=print) -> dict:
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
    }
    d = s.intel_data_dir
    step = "wait"
    try:
        wait_for_mip(wait_max_s, log)

        step = "pull"
        log("pulling the warehouse")
        with warehouse_tunnel(s.dk_warehouse_ssh, s.dk_warehouse_ssh_key, s.warehouse_local_port):
            pull = run_pull(s.dk_warehouse_dsn, d / "pulls", keep=s.intel_keep_pulls, log=lambda *_: None)
        if verify_pull(pull):
            raise RuntimeError("pull verification failed")
        rep["pull_id"] = pull.name

        step = "market"
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

        step = "ledger"
        ledger = None
        if s.intel_api_url:
            ledger, _ = fetch_ledger(s.intel_api_url, d / "ledger")
            log(f"ledger: {len(ledger)} predictions")

        step = "train"
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

        if j["verdict"] == "pass":
            step = "package"
            tgz = build_package(run_dir, d / "packages", Path.cwd())
            if verify_package(tgz):
                raise RuntimeError("package verification failed")
            rep["package"] = tgz.name
            if upload:
                step = "upload"
                upload_file(tgz, s.dk_inbox_ssh, s.dk_inbox_ssh_key)
            rep["status"] = "pass"
        else:
            rep["status"] = "fail"
    except Exception as e:
        rep.update(status="error", failed_step=step, error=f"{type(e).__name__}: {e}"[:500])
        log(traceback.format_exc())
    rep["finished_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")

    out = d / "weekly" / f"weekly-{rep['run_id']}.run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, default=str) + "\n")
    if upload and s.dk_inbox_ssh and s.dk_inbox_ssh_key:
        try:
            _upload_report(out, s)
            rep["report_uploaded"] = True
        except Exception as e:
            rep["report_uploaded"] = False
            log(f"could not upload the run report: {e}")
    return rep


def _upload_report(path: Path, s: Settings) -> None:
    from intel.package import rsync_command

    subprocess.run(rsync_command([path], s.dk_inbox_ssh, s.dk_inbox_ssh_key), check=True, capture_output=True)
