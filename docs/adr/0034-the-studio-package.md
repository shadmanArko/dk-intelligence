# 0034. The studio package: ports, settings as data, one idea becomes five native posts, budgets and resume

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
- Phase 5 asks for 150 posts per 10 days: 3 ideas a day, each adapted to Instagram, Facebook, TikTok, YouTube and
  Threads, approved on one page a day. The lab (`lab/agentic-slice/`, ADR 0031) made 3 posts for one platform.
- What did not scale in the lab: `HELPERS = 3`, weekday names for dates, one platform, `evaluate_drafts` takes at most
  20 drafts, a new MCP server process for every code call, no token cap, no way to resume a stopped run, and settings
  written into code.
- What was already right and is kept: code orchestrates and AI only judges; each agent gets its own prompt, its own
  tools and an empty folder (playbook A1-A5); code owns ids, slots, captions and prices (A20); every gate is code
  outside the AI (A12); one owner per truth (ADR 0032).

## Decision
- **A package, `src/intel/studio/`**, strict-typed, run with `uv run intel studio run|status|eval` and a console
  (`python -m intel.studio.console`, port 8771). `media.py` and `handover.py` moved here from the lab (the lab imports
  them); the lab's demo mode stays as it was.
- **Four ports** (`ports.py`): `ToolPort` (code calling MCP), `RunnerPort` (one AI judgment: today headless
  `claude -p`, later the Agent SDK or a local model), `RendererPort` (the reels CLI today, a worker later),
  `PublisherPort` (dk-publishing's HTTP API). The orchestrator touches nothing else, so each can be swapped, and the
  tests replace all four.
- **Settings as data** in `config/studio/{plan,slots,routes,budgets}.yaml`, validated at load (a typo stops the run
  before any AI is spent) and frozen into the run folder. Dates are real (today + N, with the Berlin offset across the
  clock change), never weekday names.
- **One idea, one master, five native posts.** Per idea: a copywriter (the core message, judged on the primary
  platform), a platform adapter (one call writes every platform's caption and the options it may write), and a
  designer (one master render). The platform rules reach the adapter from code (the publisher's `/v1/spec` and the
  brand playbook's hashtag range), never from its prompt. Code sets the options that must not drift (TikTok
  `privacy_level: only_me`, YouTube `made_for_kids`).
- **The scorer picks the windows.** For each day, code probes the scorer for every (format, window) and assigns the
  day's ideas to the windows that score best; an idea that passes nowhere is reported, not written.
- **Gates in code, chunked by 20:** every platform text goes through dk_intel (errors and warnings fail a caption;
  a title or on-screen line fails only on blocking errors). One repair round per idea, only for the platforms that
  failed, with exactly what the checker said; a platform that still fails is held alone and the others go out.
- **Budgets** (`budget.py`): a token cap per run and a cap of AI calls per idea. At the cap no new AI call starts,
  in-flight ones finish, and every idea not reached is reported with the reason. An alert event fires at 80%.
- **A pipeline, one render at a time.** Ideas are written 3 at a time; prepared ideas enter a render queue (one render
  at a time: it uses every core) and each finished render is uploaded while the next one renders. `caffeinate` keeps the
  Mac awake.
- **Resume.** `state.json` in the run folder is written after every change; `--resume <run id>` continues with no
  finished AI call repeated and no render repeated.
- **A persistent MCP connection per server** (`mcp.py`), one task each, instead of a process per call.
- **A new brand rule, `UNAVAILABLE_ITEM`** (dk_intel rules 1.2): a caption that names a menu item whose `available` is
  not confirmed in facts.yaml is blocked. The words come from the data: an item's own `mentions` list if it has one,
  else the words of its id and name that no other item shares ("lamb", "taster", not "kacchi").
- **Format reach is data** (`routes.yaml`): a reel reaches 5 platforms; a carousel 2 (Instagram, Facebook); one image 3
  (adds Threads, which takes a single image). The default mix is all reels (150 posts per 10 days).

## Consequences
- Good: one command makes a 10-day plan; every limit is a line in a YAML file; a stopped run costs nothing to resume;
  the budget cannot be exceeded by more than the calls already running.
- Good: the next platform, the next format or the Agent SDK is a change in one place.
- Cost / risk: `claude -p` is still a subprocess per agent (N9); the lab keeps a second copy of `call_agent`, which
  retires with the lab's demo mode.
- Risk found by running it for real: the adapter's texts are close to each other across platforms; the performance
  playbook (S4) is what should make them genuinely native, and the `feel` lines in `routes.yaml` are only a start.
- Not done here: the review page (S3, dk-publishing ADR 0025), the learning loop (S4), the schedule (S6).

## Alternatives considered
- Extend `lab/agentic-slice/run.py` in place: its tests monkeypatch module globals; at 150 posts the module would
  carry settings, ports and state together. A package with ports is cheaper to test and to change.
- One adapter call per platform: five times the context for the same idea (playbook A9: batch the judgments).
- Let the adapter choose privacy and kids settings: a setting that must not drift is code's (A20).
- A queue-and-worker renderer: the orchestrator runs on the same Mac as the footage; the `RendererPort` is the seam.
