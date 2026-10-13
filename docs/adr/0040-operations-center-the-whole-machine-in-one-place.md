# 0040. An operations center: the whole machine (ML and agents) in one place, layer by layer, with start buttons

- **Status:** Accepted
- **Date:** 2026-10-13
- Builds on [0038](0038-mission-control-and-planning-from-what-exists.md) (Mission control) and the Control Room (ADR 0004).

## Context
- The owner wanted to see every layer of the system and how deep it goes: the orchestrator, the agents it calls, which MCP
  server each agent and the code itself talk to, which tools they call, what each call received and returned; and, on the
  other side, the machine-learning run from the data to deployment with the numbers of every model. Also to start the
  whole thing with one click, or either half, or a part; and a demo page for showing it to someone.
- The ML side already writes rich files (`data/weekly/*.status.json` per step with details, `data/runs/*_<model>/report.json`
  with split, candidates, metrics and every judge gate). The agent side logged agent calls, but not the calls the
  orchestrator's own code makes to MCP servers or to the publisher, and it cut a task to 600 characters.
- Starting a run from a web page on this Mac needs more care than showing one: the console's POST buttons had no token.

## Decision
- **Record what code does too.** `trace.LoggedTools` and `LoggedPublisher` wrap the orchestrator's ports during a run, so
  every MCP call by code (`code_call`: server, tool, input, result, time, ok) and every publisher call (`publisher_call`:
  method, what, time, ok; never a payload) leaves an event next to the agents' own. The log now keeps a whole task, tool
  input, tool result and thought (12,000 characters each) instead of 300 to 600; the live feed still gets a short line.
- **Read it back as layers.** `trace.py` builds the fixed structure from the agent files and server list (phases, agents and
  the tools each may use, servers, ports) and the live numbers per layer (calls, tokens, time, errors, who called what), and
  opens any one call in full. `mlview.py` reads the 15 steps and the 7 models from the files the Sunday run already writes.
  Nothing is computed twice; nothing is changed.
- **Pages:** `/system` (a map of all layers; click a node or a call), `/ml` (the steps, the models, every number), `/ops`
  (start things), `/demo` (a made-up whole run through the same pages). One top bar on all of them.
- **Start buttons from a fixed list.** `ops.py` has six actions (all, ML run, agent run drafts or media-only, pull, market
  snapshot); each is a command the schedule or the owner already runs, run in order by a driver process that writes
  `data/studio/ops/current.json`, so a closed tab does not stop it. Page input only chooses an action id. Spending actions
  need `confirm`. The programs keep their own locks. "Run everything" runs the agent part only if the ML part succeeded.
- **The guard** (same as the Control Room): the Host header must be this Mac's own (DNS rebinding), and every POST needs a
  per-start session token that only the console's own pages are given. This also covers the older run, curate and schedule
  buttons, which had none.
- **The demo is events** fed through the same readers, so it cannot show what a real run cannot.

## Consequences
- Good: every layer is visible with real numbers; the same readers drive the live pages and the demo; the older buttons are
  safer.
- Cost / risk: run logs are larger (measured: 124 KB for a one-day run, so about 1.2 MB for ten days, up from about 0.3 MB);
  the full text of tasks and answers (brand facts, captions) sits in the local log, which already stayed on this Mac. Calls by code to the renderer's own processes
  and the publisher's payloads are not detailed (only method, time and result).
- Not done: stopping a run from the page; running a single model alone (the five insight models and the two scorers run as
  steps of the Sunday run, there is no command for one); a start button for the knowledge pack or the market-data platform's own
  refresh (a separate repo with its own schedule).

## Alternatives considered
- Extending the Control Room (its own port) with the agent side: two apps for one machine, two nav bars; the owner wants one.
- Logging every MCP call inside the MCP servers: those are separate processes and repos; wrapping the orchestrator's port
  records the same calls from the one place that makes them.
- Letting the page send a command line: a page you visit could run anything; a fixed list of ids cannot.
