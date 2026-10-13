# The Content Studio (how to run it)

One command plans 10 days, writes and designs one idea at a time, renders one master per idea, checks every word, and
hands the posts to dk-publishing as drafts. Design: [ADR 0034](adr/0034-the-studio-package.md). Plan:
[PHASE5_CONTENT_STUDIO_PLAN.md](PHASE5_CONTENT_STUDIO_PLAN.md).

```bash
uv run intel studio run --publish render          # media only: nothing is sent anywhere (needs no key)
uv run intel studio run --publish dry-run         # also validate every post with the publisher; creates nothing
uv run intel studio run --publish drafts          # create drafts for you to approve (Calendar tab, or the review page)
uv run intel studio run --days 1                  # a small run: 3 ideas, 15 posts
uv run intel studio run --resume 20261012T212440Z # continue a stopped run: no finished AI call or render repeats
uv run intel studio run --suffix r2               # redo ideas whose post was rejected (a post key can't be reused)
uv run intel studio status                        # the report of the latest run (or: status <run id>)
uv run python -m intel.studio.console             # the console: http://127.0.0.1:8771
```

## Where the settings are
All in `config/studio/`, one comment per line:

| File | What you change there |
|---|---|
| `plan.yaml` | days per run, ideas per day, which platforms, the format of idea 1, 2, 3 of a day, the pillars |
| `slots.yaml` | the three posting windows and each platform's clock time in each window |
| `routes.yaml` | which publishing format a master becomes on each platform; TikTok privacy and YouTube kids defaults; how a post "feels" on each platform |
| `budgets.yaml` | the token cap per run (4,000,000), the alert level, turns per agent, how many ideas run at once |

A typo is refused with the line to fix before anything runs.

## What a run leaves behind (`data/studio/runs/<run id>/`)
`report.md` (read this), `summary.json` (tokens per agent, seconds per phase, held reasons), `result.json` (every idea and
platform), `state.json` (the run's memory, what `--resume` reads), `events.jsonl` (the live log), `inputs.json` (what
the run read, replayable with `--inputs`), the specs, thumbnails and `config.json` (the settings it ran with).
Renders are in `dhaka-kacchi-reels/out/jobs/<post key>/`.

## Measuring a change
```bash
uv run intel studio eval freeze                   # freeze today's inputs (0 tokens)
uv run intel studio eval run --label v2 --publish render
uv run intel studio eval show                     # posts made, tokens, minutes per version
```

## One page for you: Today (start here)
```bash
uv run python -m intel.studio.console      # then open http://127.0.0.1:8771
```
Every page has the same top bar: **Today**, **Operations**, **Agent system**, **ML pipeline**, **Mission control**, **Runs**,
**Demo**, **Guide**, and **Review drafts** (the publisher's page, in a new tab).

**The whole machine (ADR 0040).** *Operations* (`/ops`) starts everything (the ML run, then the studio), or either half, or the
warehouse pull, or the market snapshot alone, from a fixed list; it asks first when tokens are spent. *Agent system* (`/system`)
is a map of every layer: orchestrator and its phases, the agents, the MCP servers, their tools and the ports (publisher,
renderer), with calls, tokens and time on every box and line; click any box, then any call, to see what it was given, which
tools it called (input, result, time), what it said and its answer. *ML pipeline* (`/ml`) shows the 15 steps of the Sunday run
and, for each of the 7 models, the data it read, the split, the candidates tried, the new model against the current one, the
confusion matrix and every judge gate. *Demo* (`/demo`) plays a made-up whole run (ML, then agents) through the same pages, no
tokens and no real data (`/system?demo=1`, `/ml?demo=1` show each half). The console now needs its own host and a session token
for every button.

**Showing how it fits together.** [HOW_IT_WORKS.md](HOW_IT_WORKS.md) (also the **How it works** page of the console) is a guided
tour with exact files and line numbers: where each agent is defined (one Markdown file each), where the orchestrator is, the
`claude -p` command that connects an agent to Claude Code and to MCP servers, where the agent system uses RAG (the orchestrator
retrieves once with `grounding_pack`; agents read cited evidence; code checks the citations), and how it runs on time (launchd,
not Claude Code's cron). The **Agent system** page has a RAG box on the map and a section "Where the agents use RAG" with the live
numbers of the last run.

**Disk space (ADR 0042).** `uv run intel studio cleanup` previews and `--apply` deletes generated videos nothing needs, old knowledge
packs and old upload copies (rules in `config/studio/retention.yaml`); the scheduled check does it once a day; Operations has a
button and a preview; Today warns under 15% free. Your footage and `out/campaign` are never reachable. The plan is 5 days (less
disk, fewer tokens per run). The VPS steps are in [runbooks/disk.md](runbooks/disk.md).

**Stop, and one model alone (ADR 0041).** *Stop* (Operations, Runs, Mission control) asks the studio run and the ML run to end
cleanly: they write "stopped by the owner", keep what is finished, and end their own agents and renderers; it takes seconds. A
stopped studio run is never restarted by the schedule; *Resume* on the Runs page continues it without redoing finished calls. A
stopped ML run counts as an attempt (the catch-up retries after 6 hours), not as the week's training. *One model alone*:
`intel weekly --only funnel --no-upload` (or the buttons on Operations and in a model's view on the ML page) trains and judges one
model on fresh data in about a minute; it uploads and deploys nothing and does not count as the week's training.

**Today** lists everything that waits for you (drafts to approve, footage proposals with Accept / Reject buttons, how far
ahead the calendar is planned, decisions, schedule) under a one-line progress strip of the current run.
**Mission control** (`/live`, ADR 0038) shows a run as an engineer would: progress and an estimate of the time left, the
pipeline with waiting / working / done counts per stage, a timeline of every agent call, every idea against every stage,
tokens per agent and the last events. `/live?demo=1` plays a made-up run through the same code (add `&tick=55` to start
partway). **Runs** starts a run and shows the history.

**Planning the next days.** A run plans from the day after what the studio has already scheduled; `intel studio coverage`
shows it, `intel studio run --publish drafts --from 2026-10-24` overrides it, and the Runs page has a "start on" field.

The daily check also sets a 9:00 reminder that appears only when something waits. Plain-words guide:
[OWNER_GUIDE.md](OWNER_GUIDE.md).

## Running without you (S6)
```bash
uv run intel studio schedule check      # would a run start now? (reads the calendar, no AI)
uv run intel studio schedule install    # a check on this Mac at login and every 30 minutes (launchd); makes DRAFTS only
uv run intel studio schedule status     # installed? what did it last do?
uv run intel studio schedule uninstall
uv run intel studio autonomy            # which kinds of post you approve most (measurement only; nothing is auto-approved)
uv run intel studio run --model designer=haiku --inputs data/studio/eval/inputs.json   # try a step on another model
```
Settings: `config/studio/schedule.yaml` (earliest time for a new run, how often to check, how far ahead, the backlog limit,
tries to finish a cut-off run), `autonomy.yaml`, `models.yaml`. The newest run's report is always
`data/studio/last-summary.md`. See [ADR 0037](adr/0037-running-unattended.md).

**A closed or crashed Mac** (ADR 0039): the check runs at login and every 30 minutes while the Mac is on. A Mac that was off at
09:30 catches up at its first check; a run that was cut off (crash, power loss) is finished by the next check from where it
stopped, up to 3 tries, then you get one notification. `intel studio schedule check` says what a check would do now; the log is
`data/studio/schedule.log`. After changing `schedule.yaml` run `intel studio schedule install` again.

## The look of each post (S5)
A creative-director agent chooses each idea's template, variant, music hint and pace, one call per day; code makes it valid
and varied. `config/studio/craft.yaml` holds how each platform's native post is made (edit a line to change the director's
taste); `config/studio/plan.yaml` `format_rotation` sets which surface the third idea of each day takes. See
[ADR 0036](adr/0036-creative-director-and-template-registry.md).
```bash
uv run intel studio curate --scope enrich --limit 5   # the curator proposes knowledge for footage (you accept in the reels repo)
uv run intel studio requests                          # what no template could do, most asked first
```

## What worked (S4)
```bash
uv run intel performance build      # after each Sunday pull: results per post, the playbook per platform (0 tokens)
uv run intel performance join       # our published studio posts joined to their results (needs the publisher key)
uv run intel performance analyst    # the weekly "what worked" note and proposals for you (one call, about 5k tokens)
```
The studio reads the latest playbook by itself: the strategist and each platform's adapter get the few lines the numbers
support (or "no evidence"), and each idea carries a `playbook_note` saying what it leans on. Read
`data/performance/<pull id>/playbook.md` and `analyst.md`. Method and limits: [ADR 0035](adr/0035-performance-learning.md).

## Rules it keeps
Drafts only: there is no approve call anywhere in the studio. A post can't change under its key (an existing key is
skipped). Nothing is dropped silently: every idea or platform that does not go out has a reason in the report.
Brand rules, prices and facts come only from dk_intel; platform rules only from the publisher's `/v1/spec`.
