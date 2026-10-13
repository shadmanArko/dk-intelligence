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

## Rules it keeps
Drafts only: there is no approve call anywhere in the studio. A post can't change under its key (an existing key is
skipped). Nothing is dropped silently: every idea or platform that does not go out has a reason in the report.
Brand rules, prices and facts come only from dk_intel; platform rules only from the publisher's `/v1/spec`.
