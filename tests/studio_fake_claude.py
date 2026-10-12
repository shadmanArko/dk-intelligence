#!/usr/bin/env python3
"""A stand-in for `claude -p … --output-format stream-json`: records what the studio passed, then answers with the
event shapes Claude Code emits. FAKE_FAIL=1 makes it exit with an error."""

import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_ARGS_LOG"], "a") as f:
    f.write(json.dumps({"args": args, "cwd": os.getcwd(), "tool_search": os.environ.get("ENABLE_TOOL_SEARCH")}) + "\n")
if os.environ.get("FAKE_FAIL"):
    print("boom", file=sys.stderr)
    sys.exit(1)
answer = {"ok": True, "task": json.loads(args[args.index("-p") + 1])}
print(
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "mcp__dk_intel_writer__evaluate_drafts",
                        "input": {"drafts": []},
                    }
                ]
            },
        }
    ),
    flush=True,
)
print(
    json.dumps(
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]}}
    ),
    flush=True,
)
print(
    json.dumps(
        {
            "type": "result",
            "is_error": False,
            "num_turns": 2,
            "result": "Done. " + json.dumps(answer),
            "usage": {
                "input_tokens": 100,
                "cache_read_input_tokens": 900,
                "cache_creation_input_tokens": 50,
                "output_tokens": 25,
            },
        }
    ),
    flush=True,
)
