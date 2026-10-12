"""Print one run as a timeline, next to what dk_intel's own audit chain recorded (lab).

uv run python lab/agentic-slice/trace.py            # the newest run
uv run python lab/agentic-slice/trace.py <run_id>
"""

from __future__ import annotations

import sys

from console import RUNS, _events, audit


def main(run_id: str | None = None) -> int:
    ids = sorted(p.stem for p in RUNS.glob("*.jsonl")) if RUNS.exists() else []
    if not ids:
        print("no runs yet: start one with run.py or the console")
        return 1
    run_id = run_id or ids[-1]
    print(f"run {run_id}\n")
    tokens = 0
    for e in _events(run_id):
        what = {
            "start": f"start ({e.get('agent', '')} {e.get('model', '')})".strip(),
            "tool_call": f"→ {e.get('server')}.{e.get('tool')}",
            "tool_result": f"← {e.get('tool')} {e.get('ms')} ms" + (" ERROR" if e.get("is_error") else ""),
            "thought": f"· {str(e.get('text', ''))[:90]}",
            "delegate": f"delegate → {', '.join(e.get('to', []))}",
            "gathered": f"gathered: {e.get('ok')} ok, {e.get('failed')} failed",
        }.get(e["kind"], "")
        if e["kind"] == "end":
            tok = int(e.get("tokens_in") or 0) + int(e.get("tokens_out") or 0)
            tokens += tok
            what = f"end {e.get('status')}" + (f" · {tok:,} tokens" if tok else "") + (
                f" · {e['error']}" if e.get("error") else "")  # fmt: skip
        print(f"{e['ms'] / 1000:7.1f}s  {e['node']:<13} {what}")
    print(f"\ntokens: {tokens:,}")
    try:
        entries = audit(run_id)
    except Exception as ex:  # noqa: BLE001 - the audit is a second opinion; the trace stands without it
        print(f"dk_intel audit: unavailable ({ex})")
        return 0
    print(f"\ndk_intel audit chain: {len(entries)} tool calls in this run's window")
    for a in entries:
        print(f"  {a['at']}  {a['profile']:<8} {a['name']:<20} {a['status']}  {a['ms']} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
