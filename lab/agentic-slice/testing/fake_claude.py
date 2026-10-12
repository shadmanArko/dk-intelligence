#!/usr/bin/env python3
"""A stand-in for `claude -p … --output-format stream-json` (tests and offline demos): it answers each lab agent with
the same event shapes Claude Code emits (init, tool_use, tool_result, text, result). Set FAKE_FAIL_SLOT=s2 to make
the writer helper for that slot fail, FAKE_DELAY_S to slow it down (to see the console move)."""

import json
import os
import sys
import time

args = sys.argv[1:]
prompt = args[args.index("--append-system-prompt") + 1]
task = args[args.index("-p") + 1]
tools = args[args.index("--allowedTools") + 1].split(",")
delay = float(os.environ.get("FAKE_DELAY_S", "0.3"))
with open(os.environ.get("FAKE_ARGS_LOG", os.devnull), "a") as f:  # what the engine passed, for the tests
    f.write(
        json.dumps(
            {
                "tools": tools,
                "builtins": args[args.index("--tools") + 1],
                "task": task,
                "tool_search": os.environ.get("ENABLE_TOOL_SEARCH"),
            }
        )
        + "\n"
    )


def out(e):
    print(json.dumps(e), flush=True)


def tool(name, inp, result):
    call_id = f"{name}-{time.time_ns()}"
    out(
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": call_id, "name": name, "input": inp}]}}
    )
    time.sleep(delay)
    out(
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": call_id, "content": json.dumps(result)}]},
        }
    )


out({"type": "system", "subtype": "init", "tools": tools})
if "PLANNER" in prompt:
    tool(tools[3], {"platform": "instagram"}, {"slots": ["s1", "s2", "s3"]})
    tool(tools[0], {"platform": "instagram", "days": 7}, {"occasions": []})
    answer = {
        "slots": [
            {"slot_id": f"s{i}", "day": d, "time": "17:00", "format": "reel", "angle": a, "sources": ["R-AAAAAAAA"]}
            for i, (d, a) in enumerate([("Tue", "rice"), ("Thu", "delivery"), ("Sat", "meat")], start=1)
        ]
    }
elif "WRITER helper" in prompt:
    slot = json.loads(task)["slot_id"]
    if os.environ.get("FAKE_FAIL_SLOT") == slot:
        time.sleep(delay)
        print("simulated failure", file=sys.stderr)
        sys.exit(1)
    tool(
        tools[0],
        {"drafts": [{"platform": "instagram", "caption": "Samstag Kacchi in Berlin"}]},
        {"results": [{"verdict": "keep"}]},
    )
    answer = {
        "slot_id": slot,
        "caption": "Samstag gibt es Kacchi in Berlin.",
        "verdict": "keep",
        "score": 0.6,
        "rounds": 1,
        "sources": ["R-AAAAAAAA"],
    }
else:  # writer-lead
    results = json.loads(task)["results"]
    for r in results:
        tool(tools[1], {"slot_id": r["slot_id"]}, {"proposal_id": "p-" + r["slot_id"]})
    answer = {
        "proposed": [{"slot_id": r["slot_id"], "proposal_id": "p-" + r["slot_id"]} for r in results],
        "skipped": [],
    }
out({"type": "assistant", "message": {"content": [{"type": "text", "text": json.dumps(answer)}]}})
out(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 3,
        "result": json.dumps(answer),
        "usage": {"input_tokens": 1200, "output_tokens": 300, "cache_read_input_tokens": 0},
    }
)
