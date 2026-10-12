# 0031. Agents design the picture as a spec; code renders it; the publisher receives drafts

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
- The lab (ADR-less, `lab/agentic-slice/`) plans posts and writes captions. Two other systems already exist:
  `dhaka-kacchi-reels` renders branded reels and slides from a JSON `ContentSpec` (Remotion, on the Mac), and
  `dk-publishing` validates, drafts, approves and publishes (VPS). Nothing connected them.
- The owner's plan: an orchestrator with sub-agents, one of which designs the post's media, and the system hands the
  result to publishing. Constraints: €0 and no paid AI API (ADR 0012), nothing publishes without the owner's approval,
  the render needs 3 GB of footage that lives on the Mac.
- **Measured before deciding (2026-10-12):**
  - the reels CLI copied the whole `public/` folder (3.5 GB) into the temp folder on every run and never removed it:
    six runs filled the disk;
  - one render is 20–25 s of every CPU core; a two-format reel took 54 s;
  - an agent given a spec to fix finished in 13 s and 8.9k tokens once its prompt was right (4 turns and 20.6k before).

## Decision
1. **Agents write intent, never code per post.** A *designer* agent (AI, one per caption that passed) writes only the
   creative part of a ContentSpec: hook, shots or slides, on-screen words. It has three read-only tools of the new
   `dk_reels` MCP server (`creative` profile: `search_assets`, `search_music`, `preview_spec`) and **no render tool, no
   shell and no files**: a render is minutes of CPU, so an agent that could call it could loop on it.
2. **Code owns everything an AI must not control:** the job id, the format and size the platform needs, the slot time,
   the post captions, the footage used recently. The designer's answer is data (playbook A13): `finalize_spec` takes
   its creative content and overwrites the rest. A test proves a designer that tries to set the schedule is ignored.
3. **The render is plain code through the engine's own CLI** (`npm run job`: exit codes and a JSON last line), one
   render at a time. The reels engine became a library (`runJob`) with a thin CLI and the MCP server on top.
4. **Hand-over is plain code, 0 tokens, drafts only** (`handover.py`): upload each file once (content-addressed),
   one `POST /v1/posts/batch` after a validating dry run, checked first against the live `GET /v1/spec`. There is no
   approve call anywhere in the lab: the owner ticks approve in the Calendar. `post_key` = `job_id`.
5. **Nothing is dropped silently.** A post or platform that cannot go out is returned as *held* with a reason: failed
   QA, an on-screen language no native speaker has checked (`handover.json → reviewed_languages`), a bad slot, a platform
   that is off, a caption over the limit, a required option nobody supplies.
6. **Brand and claims rules have one home: dk_intel.** The renderer exports every on-screen string in its manifest and
   code runs them through `evaluate_drafts`, acting on blocking errors only. The renderer holds no brand rules.
7. **Pre-check before spending AI.** Before any writer or designer runs, code asks the publisher whether each slot's
   post already exists and skips it. (A rerun would otherwise produce a different post under the same key, which the
   publisher correctly refuses.)
8. **Four modes, default unchanged:** `--publish demo` (as before), `render` (media only, needs no key, sends
   nothing), `dry-run` (validates with the publisher, creates nothing), `drafts`.
9. **Footage variety** travels in the spec as `avoid_assets` (clips used in the last 12 jobs plus the ones this run
   already planned), so the resolver stays pure and deterministic.
10. **Music is normalised to −14 LUFS in the renderer** by measuring the exact segment the cut uses.

## Alternatives considered
- **A Postgres render queue and a worker pulling jobs (the reels plan, phase 3).** Rejected for now: the orchestrator
  runs on the Mac next to the footage, so a queue only adds a failure mode. It becomes right if the orchestrator moves
  to the VPS (a `RenderPort` seam keeps that cheap).
- **Google Drive as the media path.** Rejected: the publisher's `POST /v1/media` postdates that plan and removes a hop.
- **Letting agents call a render tool** (MCP `ops` profile exists for interactive use and code). Rejected for the
  autonomous designer: cost trap, no benefit over `preview_spec`.
- **An AI orchestrator.** Rejected (playbook §1): code orchestrates, AI works inside steps.

## Consequences
- A weekly run is about 24k tokens and 58 s per finished post (two real runs, below), on the subscription: €0.
- The lab is no longer read-only: with `--publish drafts` it creates drafts in the owner's Calendar. Everything else is
  unchanged; `render` and `dry-run` write nothing outside the Mac.
- **Not verified live:** nothing has been handed to the real publisher (no draft key yet). The bridge is tested against a
  fake that enforces the contract's rules (formats, file counts, caption limits, required options, idempotent keys,
  expiring media); the first live run is a `dry-run`, then one draft the owner approves or rejects.
- **A finding the pipeline exposes:** the planner chose "reassure on order accuracy" from a competitor gap, the writer
  wrote "we personally check every single Kacchi before it leaves our kitchen", and dk_intel passed it, so an unverified
  process claim reached the picture ("Checked. Confirmed."). dk_intel's rules do not cover process claims. The designer
  prompt now forbids them; the writer and the rules are the owner's call (see the results note).
- Next: the template-writing loop (a separate agent that writes new templates as code, behind gates and the owner's
  approval) needs the template registry first. It is milestone 9.
