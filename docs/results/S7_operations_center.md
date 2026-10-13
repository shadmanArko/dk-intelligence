# S7 results: the whole machine in one place (measured 2026-10-13, real runs)

What was built: [ADR 0040](../adr/0040-operations-center-the-whole-machine-in-one-place.md). Pages: `/ops`, `/system`, `/ml`,
`/demo` of the Studio console (`uv run python -m intel.studio.console`). How to use them: [STUDIO.md](../STUDIO.md).

## What the pages show, on real data
- **Agent system** (`/system`). On a real one-day run (`20261013T150727Z`, render mode, 3 ideas): 5 phases, 5 agents (11 calls,
  85k tokens), 3 MCP servers, 7 tools, 2 ports. Every call can be opened: the task the agent was given, each tool it called
  with input, result and time, what it said, its answer, its retries.
- **ML pipeline** (`/ml`), on the real run of 2026-10-11: the 15 steps with what each did (e.g. `pull`: 40 tables, 13,871 rows,
  18 s) and the 7 models. For a model: the data it read, the pull and market snapshot it used, the train and test split (306 and
  77 posts, test = posts after 2026-08-28), 5 candidates tried, the new model against the current one with its 95% range
  (0.6991 against 0.6741; gain +0.025, range -0.043 to +0.093), the confusion matrix, the platform split and each of the 7 judge
  gates (6 pass, 1 pending live evidence).
- **Operations** (`/ops`): six actions from a fixed list. Tested for real with the read-only warehouse pull from the page: it
  started under the driver, showed as running, finished `done` with exit 0 and made a new immutable pull snapshot in about 30 s.
- **Demo** (`/demo`): a made-up whole run (15 ML steps, then 12 ideas through the agents), 0 tokens, drawn by the same pages
  from synthetic events.

## What the new recording found out (the first time code's own calls were visible)
The same one-day run, split by who made the call:

| | calls | notes |
|---|---|---|
| Agents' own tool calls | 6 | 3 `evaluate_drafts` by copywriters, 3 `preview_spec` by designers |
| The orchestrator's own MCP calls | **17** | `brand_rules` x5, `grounding_pack`, `get_facts`, `list_templates`, `inventory`, and `evaluate_drafts` x8 (the probe scores and the per-platform brand checks) |
| Publisher calls | 1 | `spec` (73 ms); drafts calls appear in a drafts run |
| Renders | 3 | |

So **two thirds of the MCP traffic is code, not agents**: the brand check and the scorer run in code between the agents,
which is the design (ADR 0034), and it had no trace before today. The first call to each MCP server pays its start-up:
`list_templates` 5.1 s then `inventory` 4 ms on the same server; `grounding_pack` 10.7 s; the whole inputs phase 11 s, because
the start-ups run in parallel.

Log size: 86 events, **124 KB** for one day (about 1.2 MB for ten days). Before, a task was cut to 600 characters and a tool
input or result to 300; now 12,000, so a whole call can be read.

## The guard
Running things from a page needs more care than showing them. Measured from the command line against the live console:
no token 403, wrong token 403, a foreign Host header 400. The older buttons (run, curate, schedule, analyst) had no token before
and are covered now. The test file `tests/test_operations.py` (11 tests) covers recording, the trace readers, the ML reader, the
driver (order, stop at first failure, a dead driver shown as stopped), the fixed action list, the guard and every page.

## What is not there
- Stopping a run from the page.
- Running one model alone: the 5 insight models and the 2 scorers are steps of the Sunday run; there is no command for one.
- A button for the market-data platform's own refresh: another repo with its own schedule.
- "Run everything" was not run end to end (about 13 minutes and 650,000 tokens on top of the ML run); its two halves were each
  run for real before, and its driver (steps in order, stop at the first failure) is tested.
