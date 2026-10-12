"""Measure, don't guess (lab): run the engine on the SAME frozen week and compare versions on quality and cost.

    uv run python lab/agentic-slice/eval.py freeze --platform instagram    # this week's slots + grounding pack (no AI)
    uv run python lab/agentic-slice/eval.py run --label v3 --runs 2        # real runs on the frozen inputs
    uv run python lab/agentic-slice/eval.py add <run_id> --label v2        # count an earlier run too
    uv run python lab/agentic-slice/eval.py show                           # the comparison table

Quality = how many of the 3 posts pass dk_intel's check ("keep") and their content-scorer chance; cost = tokens and
seconds. The frozen inputs and results stay in `eval/` (git-ignored: they hold review quotes and our posts).
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

LAB = Path(__file__).resolve().parent
EVAL = LAB / "eval"
RESULTS = EVAL / "results.jsonl"
sys.path.insert(0, str(LAB))

import console  # noqa: E402 - the lab folder is not a package
import run as engine  # noqa: E402


def inputs_path(platform: str) -> Path:
    return EVAL / f"inputs-{platform}.json"


def freeze(platform: str) -> Path:
    (slots,) = engine.mcp_session("demo_publisher", [("list_slots", {"platform": platform})])
    (pack,) = engine.mcp_session("dk_intel_planner", [("grounding_pack", {"platform": platform, "days": 7})])
    pack.pop("prov", None)
    EVAL.mkdir(exist_ok=True)
    out = inputs_path(platform)
    out.write_text(json.dumps({"slots": slots.get("slots", []), "pack": pack}, ensure_ascii=False, indent=1))
    return out


def measure(run_id: str, label: str) -> dict[str, Any]:
    ev = console._events(run_id)
    r = console.result_of(run_id, ev)
    posts, s = r["posts"], r["run"]
    scores = [float(p["score"]) for p in posts if isinstance(p.get("score"), int | float)]
    kept = [float(p["score"]) for p in posts if p["status"] == "proposed" and isinstance(p.get("score"), int | float)]
    row = {"label": label, "run": run_id, "platform": s["platform"], "inputs": ev[0].get("inputs", "live"),
           "proposed": sum(p["status"] == "proposed" for p in posts), "posts": len(posts),
           "mean_score": round(statistics.mean(scores), 3) if scores else None,
           "mean_score_proposed": round(statistics.mean(kept), 3) if kept else None,
           "tokens": s["tokens"], "seconds": s["seconds"], "tool_calls": s["tool_calls"],
           "status": s["status"]}  # fmt: skip
    EVAL.mkdir(exist_ok=True)
    with RESULTS.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def show() -> None:
    rows = [json.loads(line) for line in RESULTS.read_text().splitlines()] if RESULTS.exists() else []
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["label"], []).append(r)
    print(f"{'version':<24}{'runs':>5}{'proposed':>10}{'mean score':>12}{'kept score':>12}{'tokens':>9}{'seconds':>9}")
    for label, rs in by.items():

        def avg(k: str, rs: list[dict[str, Any]] = rs) -> float | None:
            v = [r[k] for r in rs if r.get(k) is not None]
            return statistics.mean(v) if v else None

        prop = f"{sum(r['proposed'] for r in rs)}/{sum(r['posts'] for r in rs)}"
        ms, mk, tk, sc = avg("mean_score"), avg("mean_score_proposed"), avg("tokens"), avg("seconds")
        print(f"{label:<24}{len(rs):>5}{prop:>10}{(f'{ms:.0%}' if ms else '-'):>12}{(f'{mk:.0%}' if mk else '-'):>12}"
              f"{(f'{tk / 1000:.0f}k' if tk else '-'):>9}{(f'{sc:.0f}' if sc else '-'):>9}")  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--platform", default="instagram", choices=["instagram", "facebook"])
    r = sub.add_parser("run")
    r.add_argument("--label", required=True)
    r.add_argument("--runs", type=int, default=2)
    r.add_argument("--platform", default="instagram", choices=["instagram", "facebook"])
    a_ = sub.add_parser("add")
    a_.add_argument("run_id")
    a_.add_argument("--label", required=True)
    sub.add_parser("show")
    a = ap.parse_args()
    if a.cmd == "freeze":
        print(f"frozen: {freeze(a.platform)}")
    elif a.cmd == "add":
        print(measure(a.run_id, a.label))
    elif a.cmd == "run":
        frozen = inputs_path(a.platform)
        if not frozen.exists():
            print(f"no frozen inputs: run `eval.py freeze --platform {a.platform}` first", file=sys.stderr)
            return 1
        for i in range(a.runs):
            before = {p.stem for p in console.RUNS.glob("*.jsonl")}
            subprocess.run([sys.executable, str(LAB / "run.py"), "--platform", a.platform, "--trigger", "eval",
                            "--inputs", str(frozen)], check=False)  # fmt: skip
            new = sorted({p.stem for p in console.RUNS.glob("*.jsonl")} - before)
            if new:
                print(f"run {i + 1}/{a.runs}: {measure(new[-1], a.label)}")
    show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
