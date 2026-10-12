#!/usr/bin/env python3
"""A stand-in for `claude -p … --output-format stream-json` (tests and offline demos): it answers each lab agent with
the same event shapes Claude Code emits (init, tool_use, tool_result, text, result). Set FAKE_FAIL_SLOT=s2 to make
the writer helper for that slot fail, FAKE_DELAY_S to slow it down (to see the console move)."""

import json
import os
import sys
import time

args = sys.argv[1:]
prompt = args[args.index("--system-prompt") + 1]
task = args[args.index("-p") + 1]
tools = args[args.index("--allowedTools") + 1].split(",") if "--allowedTools" in args else []
delay = float(os.environ.get("FAKE_DELAY_S", "0.3"))
with open(os.environ.get("FAKE_ARGS_LOG", os.devnull), "a") as f:  # what the engine passed, for the tests
    f.write(
        json.dumps(
            {
                "tools": tools,
                "builtins": args[args.index("--tools") + 1],
                "task": task,
                "tool_search": os.environ.get("ENABLE_TOOL_SEARCH"),
                "cwd": os.getcwd(),
                "hidden": args[args.index("--disallowedTools") + 1].split(",") if "--disallowedTools" in args else [],
            }
        )
        + "\n"
    )


def named(short):
    return next(t for t in tools if t.endswith("__" + short))


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
    given = json.loads(task)  # the engine hands the planner its data: no tools, one turn
    angles = ["rice", "delivery", "meat"]
    answer = {
        "slots": [
            {
                "id": s["slot_id"],  # `id`, not `slot_id`: as the real planner once answered
                "day": s["day"],
                "time": s["time"],
                "format": s["format"],
                "angle": a,
                "sources": ["R-AAAAAAAA"],
            }
            for s, a in zip(given["slots"], angles, strict=False)
        ]
    }
elif "WRITER helper" in prompt:
    slot = json.loads(task)["slot_id"]
    if os.environ.get("FAKE_FAIL_SLOT") == slot:
        time.sleep(delay)
        print("simulated failure", file=sys.stderr)
        sys.exit(1)
    tool(
        named("evaluate_drafts"),
        {"drafts": [{"ref": r, "platform": "instagram", "caption": "Samstag Kacchi in Berlin"} for r in "abc"]},
        {"results": [{"verdict": "keep"}]},
    )
    rewrite = os.environ.get("FAKE_REWRITE_SLOT") == slot
    answer = {
        "slot_id": slot,
        "caption": "Samstag gibt es Kacchi in Berlin.",
        "verdict": "rewrite" if rewrite else "keep",
        "score": 0.6,
        "rounds": 1,
        "tried": 3,
        "sources": ["R-AAAAAAAA"],
    }
elif "DESIGNER" in prompt:
    brief = json.loads(task)
    job, make = brief["job_id"], brief["make"]
    halal_slot = os.environ.get("FAKE_HALAL_SLOT")
    hook = "100% halal kacchi" if halal_slot and job.endswith("-" + halal_slot) else "Sealed. Then opened."
    wrong = os.environ.get("FAKE_WRONG_KIND_SLOT") and job.endswith("-" + os.environ["FAKE_WRONG_KIND_SLOT"])
    if make == "video" and not wrong:
        content = {
            "kind": "video", "template": "beat-reel", "hook": hook, "hook_sub": "Deckel ab, Kacchi drin",
            "music": {"track": "auto", "culture": "desi", "mood": ["hip-hop"], "pre_drop_beats": 8},
            "shots": [{"category": "Marination"}, {"category": "Pot opening", "min_rating": 2, "on": "drop",
                      "caption": "Lid off", "sub": "Deckel ab"}, {"category": "Eating"}],
        }  # fmt: skip
    else:
        content = {
            "kind": "slides",
            "size": "carousel",
            "slides": [{"template": "photo", "category": "Eating", "title": hook}],
        }
    spec = {
        "spec_version": 1, "job_id": job, "language": ["en", "de"], "content": content,
        "captions": {"instagram": "the designer must not set this"},
        "publish": {"platforms": ["tiktok"], "at": "2020-01-01T00:00:00+00:00"},
    }  # fmt: skip
    tool(named("preview_spec"), {"spec": spec}, {"ok": True, "plan": {}})
    answer = {"job_id": job, "spec": spec, "why": "the lid coming off"}
else:  # writer-lead
    results = json.loads(task)["results"]
    for r in results:
        tool(named("propose_post"), {"slot_id": r["slot_id"]}, {"proposal_id": "p-" + r["slot_id"]})
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
