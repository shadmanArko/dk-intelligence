"""The machine-learning layer, step by step and model by model, for the Operations pages (ADR 0040).

Everything is read from files the Sunday run already writes: `data/weekly/` (the run's progress and report, one JSON per
step with its own details), `data/runs/<time>_<model>/report.json` (what each model was trained on, its metrics, the
judge's gates) and `insights.json`. Nothing is computed twice and nothing is changed here.

    ml_state(...)    the 15 steps with their status, timing and details; the 7 models with their latest result
    model_detail()   one model in full: split, candidates, test metrics, every judge gate, the top of its output
    ml_demo(tick)    a made-up Sunday run in the same shape, for showing the page without a real run

Pure readers: easy to test with a folder of fixtures.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import yaml

from intel.pipeline import definition

MODELS_DIR = Path(__file__).parent / "models"
NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
INSIGHTS_LIMIT = 150_000  # characters of insights.json the page may ask for


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def model_specs(models_dir: Path = MODELS_DIR) -> list[dict[str, Any]]:
    """The models that exist, from their spec.yaml (name, what it answers, what data it reads)."""
    out = []
    for f in sorted(models_dir.glob("*/spec.yaml")):
        spec = yaml.safe_load(f.read_text()) or {}
        spec.setdefault("name", f.parent.name)
        spec["kind"] = spec.get("kind") or "scorer"
        out.append(spec)
    return out


def latest_run(runs_dir: Path, name: str) -> Path | None:
    folders = sorted(runs_dir.glob(f"*_{name}"))
    return folders[-1] if folders and (folders[-1] / "report.json").exists() else None


def _brief_test(test: dict[str, Any] | None) -> dict[str, Any]:
    t = test or {}
    keep = ("n", "balanced_accuracy", "balanced_accuracy_ci95", "roc_auc", "brier", "positive_rate", "confusion_matrix",
            "per_platform")  # fmt: skip
    return {k: t[k] for k in keep if k in t}


def model_card(spec: dict[str, Any], run: Path | None) -> dict[str, Any]:
    """One model as the page shows it: what it answers and the latest result."""
    card: dict[str, Any] = {"name": spec["name"], "kind": spec["kind"], "question": spec.get("question"),
                            "data": spec.get("data"), "label": spec.get("label"),
                            "prediction_time": spec.get("prediction_time"), "run": None}  # fmt: skip
    report = _load(run / "report.json") if run else None
    if not report:
        return card
    judge = report.get("judge") or {}
    challenger = report.get("challenger") or {}
    card["run"] = {
        "folder": run.name if run else None,
        "version": report.get("version"),
        "created_at": report.get("created_at"),
        "pull_id": report.get("pull_id"),
        "market_id": report.get("market_id"),
        "split": report.get("split"),
        "verdict": judge.get("verdict"),
        "gain": judge.get("gain"),
        "gain_ci95": judge.get("gain_ci95"),
        "failed": judge.get("failed") or [],
        "gates": judge.get("gates") or [],
        "challenger": {
            "model": challenger.get("model"),
            "params": challenger.get("params"),
            "features": challenger.get("features"),
            "cv": challenger.get("cv_balanced_accuracy"),
            "test": _brief_test(challenger.get("test")),
        },  # fmt: skip
        "champion": {
            "model": (report.get("champion") or {}).get("model"),
            "test": _brief_test((report.get("champion") or {}).get("test")),
        },  # fmt: skip
        "leaderboard": (report.get("leaderboard_cv") or [])[:6],
        "top_complaints": (report.get("top_complaints") or [])[:5],
    }
    return card


def model_detail(name: str, runs_dir: Path, models_dir: Path = MODELS_DIR) -> dict[str, Any] | None:
    """One model in full. `name` must be a model that exists (never a path)."""
    spec = next((s for s in model_specs(models_dir) if s["name"] == name), None)
    if spec is None or not NAME.match(name):
        return None
    run = latest_run(runs_dir, name)
    out: dict[str, Any] = {"card": model_card(spec, run), "spec": spec, "report": None, "insights_keys": []}
    if run:
        out["report"] = _load(run / "report.json")
        ins = _load(run / "insights.json")
        if isinstance(ins, dict):
            out["insights_keys"] = [{"key": k, "size": len(json.dumps(v, default=str))} for k, v in ins.items()]
        out["files"] = sorted(p.name for p in run.iterdir())
    return out


def insights_text(name: str, runs_dir: Path, models_dir: Path = MODELS_DIR) -> str | None:
    if not any(s["name"] == name for s in model_specs(models_dir)):
        return None
    run = latest_run(runs_dir, name)
    f = run / "insights.json" if run else None
    return f.read_text()[:INSIGHTS_LIMIT] if f and f.exists() else None


def _seconds(step: dict[str, Any]) -> float | None:
    if step.get("started_at") and step.get("finished_at"):
        a, b = dt.datetime.fromisoformat(step["started_at"]), dt.datetime.fromisoformat(step["finished_at"])
        return round((b - a).total_seconds(), 1)
    return None


def steps_view(progress: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The pipeline's steps, in order, each with what it does and what it did (status, time, note, details)."""
    done = (progress or {}).get("steps") or {}
    partial = bool((progress or {}).get("only"))
    out = []
    for s in definition()["steps"]:
        run = done.get(s["key"]) or {}
        if partial and s["key"] not in done:  # a single-model run: the other models' steps are simply not part of it
            run = {"status": "skipped", "note": "not part of this single-model run"}
        out.append({"key": s["key"], "label": s["label"], "desc": s["desc"], "status": run.get("status") or "pending",
                    "started_at": run.get("started_at"), "finished_at": run.get("finished_at"),
                    "seconds": _seconds(run), "note": run.get("note"),
                    "details": run.get("details") or {}})  # fmt: skip
    return out


def progress_of(weekly_dir: Path) -> dict[str, Any] | None:
    """The run in progress, else the newest finished one."""
    cur = _load(weekly_dir / "current.json")
    if isinstance(cur, dict) and cur.get("steps"):
        return cur
    files = sorted(weekly_dir.glob("weekly-*.status.json"))
    last = _load(files[-1]) if files else None
    return last if isinstance(last, dict) else None


def ml_state(weekly_dir: Path, runs_dir: Path, running: bool, history: list[dict[str, Any]],
             log: list[str]) -> dict[str, Any]:  # fmt: skip
    prog = progress_of(weekly_dir)
    steps = steps_view(prog)
    stalled = bool(prog and prog.get("state") == "running" and not running)
    n_done = sum(1 for s in steps if s["status"] in ("done", "skipped"))
    return {
        "run": {
            "id": (prog or {}).get("run_id"),
            "state": (prog or {}).get("state"),
            "started_at": (prog or {}).get("started_at"),
            "updated_at": (prog or {}).get("updated_at"),
            "current_step": (prog or {}).get("current_step"),
            "running": running,
            "stalled": stalled,
            "only": (prog or {}).get("only"),
        },  # fmt: skip
        "percent": round(100 * n_done / len(steps), 1) if steps else 0.0,
        "steps": steps,
        "models": [model_card(s, latest_run(runs_dir, s["name"])) for s in model_specs()],
        "history": history,
        "log": log,
    }


# ---------- a made-up Sunday run, in the same shape ----------

DEMO_STEP_TICKS = 4  # one step of the demo takes this many polls


def _demo_run(spec: dict[str, Any], verdict: str, judged: bool) -> dict[str, Any]:
    gates = [{"gate": "data", "status": "pass", "detail": "made-up"}] if judged else []
    return {"folder": "demo", "version": f"{spec.get('version_prefix', 'x')}-demo", "created_at": None,
            "pull_id": "demo", "market_id": "demo", "split": {"train": 306, "test": 77},
            "verdict": verdict if judged else None, "gain": 0.025 if judged else None, "gain_ci95": None,
            "failed": [], "gates": gates, "leaderboard": [], "top_complaints": [],
            "challenger": {"model": "demo", "params": {}, "features": "demo", "cv": 0.59, "test": {}},
            "champion": {"model": "demo", "test": {}}}  # fmt: skip


def ml_demo(tick: int) -> dict[str, Any]:
    """A believable run for showing the page without one. Same shape as `ml_state`; every number is made up."""
    base = dt.datetime(2026, 10, 11, 10, 0, 0, tzinfo=dt.UTC)
    steps = definition()["steps"]
    total = len(steps) * DEMO_STEP_TICKS
    t = max(0, tick) % (total + 12)
    idx = min(t // DEMO_STEP_TICKS, len(steps))
    notes = {"pull": "40 tables, 13,871 rows", "market": "snapshot demo-1", "ledger": "26 predictions",
             "train": "2 models trained", "mine": "13,698 reviews", "community": "542 areas scored",
             "occasions": "5 occasions", "funnel": "284 visits → 3 orders", "creators": "109 creators ranked",
             "judge": "5 pass, 1 fail, 1 kept", "package": "5 packages", "upload": "5 arrived intact",
             "knowledge": "kp-demo is live", "report": "sent"}  # fmt: skip
    done: dict[str, Any] = {}
    for i, s in enumerate(steps):
        if i < idx:
            start = base + dt.timedelta(seconds=25 * i)
            done[s["key"]] = {"label": s["label"], "status": "done", "started_at": start.isoformat(),
                              "finished_at": (start + dt.timedelta(seconds=20)).isoformat(),
                              "note": notes.get(s["key"], "done"), "details": {"demo": "made-up numbers"}}  # fmt: skip
        elif i == idx and idx < len(steps):
            began = (base + dt.timedelta(seconds=25 * i)).isoformat()
            done[s["key"]] = {"label": s["label"], "status": "running", "started_at": began, "details": {}}
    prog = {"run_id": "demo", "state": "running" if idx < len(steps) else "finished", "steps": done,
            "started_at": base.isoformat(), "updated_at": base.isoformat(),
            "current_step": steps[idx]["key"] if idx < len(steps) else None}  # fmt: skip
    view = steps_view(prog)
    cards = []
    for s in model_specs():
        step_key = {"content_scorer": "train", "reach_scorer": "train", "weakness_miner": "mine",
                    "community_map": "community", "occasion_planner": "occasions", "funnel": "funnel",
                    "creator_matcher": "creators"}.get(s["name"], "train")  # fmt: skip
        finished = next((x["status"] == "done" for x in view if x["key"] == step_key), False)
        judged = next((x["status"] == "done" for x in view if x["key"] == "judge"), False)
        card = {"name": s["name"], "kind": s["kind"], "question": s.get("question"), "data": s.get("data"),
                "label": s.get("label"), "prediction_time": s.get("prediction_time"), "run": None}  # fmt: skip
        if finished:
            card["run"] = _demo_run(s, "fail" if s["name"] == "reach_scorer" else "pass", judged)
        cards.append(card)
    running = idx < len(steps)
    n_done = sum(1 for x in view if x["status"] == "done")
    return {"run": {"id": "demo", "state": prog["state"], "started_at": prog["started_at"], "updated_at": None,
                    "current_step": prog["current_step"], "running": running, "stalled": False},  # fmt: skip
            "percent": round(100 * n_done / len(view), 1), "steps": view, "models": cards, "history": [],
            "log": [f"demo: step {max(idx, 1)} of {len(view)}"], "demo": True}  # fmt: skip
