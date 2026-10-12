# M8: from a planned slot to a finished post (2026-10-12)

Real numbers from real runs on the Mac. ADR: [0031](../adr/0031-agents-design-code-renders-publisher-gets-drafts.md).
One frozen week (the same inputs that produced the v6 numbers in the lab README), Instagram, 3 slots.

## What was built
| Where | What |
|---|---|
| `dhaka-kacchi-reels` (new local git repo, 6 commits) | `runJob()` as a library; a cached Remotion bundle; the **`dk_reels` MCP server** (profiles `creative`: `list_templates`, `inventory`, `search_assets`, `search_music`, `preview_spec`; `ops`: `render_spec`, `job_status`); `avoid_assets`; every on-screen text in the manifest; music normalised to −14 LUFS; 18 fast tests; a render regression script |
| `lab/agentic-slice/` | `handover.py` (manifest → drafts at dk-publishing); `media.py` (what code decides around the designer); the **designer** agent; `--publish render\|dry-run\|drafts`; console cards with thumbnails and a media layout of the live graph |
| tests | 21 handover, 20 media, 23 engine/console (all lab tests: 64), 18 reels. Five safety rules were mutation-tested (language hold, validating dry run, brand check, already-exists pre-check, code owning the schedule): breaking each made the intended test fail |

## Measured
**The designer agent** (one real run each, same brief, a reel about the sealed pot):
| | turns | tokens in / out | time | previews |
|---|---|---|---|---|
| first prompt | 4 | 20.6k / 2.6k | 27 s | 3 (2 refused: an `on` key on every shot; key-moment footage asked of a category that has none) |
| after two prompt fixes | 2 | 8.9k / 1.0k | 13 s | 1, accepted first time |

The agent repaired both refusals from the exact error paths, so the self-repair loop works; the fix was to stop it
needing the loop. One run each is an indication, not a benchmark.

**The whole chain, real model, real dk_intel, real renders** (`--publish render`, frozen week):
| run | posts rendered | tokens | wall time | note |
|---|---|---|---|---|
| A | 1 of 2 | 40.7k | 101 s | the second post was lost to the loudness bug below |
| B | **2 of 2** that can pass (the third slot is the known Saturday-carousel slot, dropped by the pre-check) | 48.1k | 117 s | two renders took 45 s |

Per finished post: about **24k tokens and 58 s** (run B). For comparison the lab's v6 (captions only, no media) was 21k
tokens and 35 s per run. By step in run B: planner 4.4k, two writers 9.0k and 15.7k, two designers 9.7k and 9.4k,
code steps 0 tokens. On the subscription that is €0; at the playbook's assumed API prices (about $2 / $10 per 1M in/out)
run B would be about $0.15, so $0.08 per post. Prices change: recompute from the token numbers.

**The renderer** (it was a script that shelled out; now a library):
| | before | after |
|---|---|---|
| 3 stills | 9.4 s | 3.8 s |
| a 2-format reel | 54 s | 37 s |
| the same spec again | 54 s | 0.5 s (cached) |
| disk per run | **+3.5 GB, never freed** | +0 (one 26 MB bundle) |

Stills are bit-identical to before. Videos are not bit-exact even between two runs of the same code (Remotion picks a
neighbouring source frame near a clip start; checked on frame 1), so `scripts/regress.sh` checks videos against the
measured band (average ≥ 45 dB, worst frame ≥ 25 dB) and says so. The original code was sampled only four times, so
"same noise as before" is likely, not proven.

## What running it for real found (none of these showed up in the fake-model tests)
1. **A disk leak.** Every render copied `public/` (3.5 GB) into the temp folder and never removed it. My own probing
   filled the disk twice. A weekly cron would have done it within days. Fixed: a bundle cached by source hash, with
   `public/` linked rather than copied.
2. **`--dry-run` hid the on-screen texts** for any spec that had been rendered before (the cache answered first). Those
   texts are exactly what the brand check reads. Found by driving the real CLI from the lab; now a failing-then-passing
   test.
3. **Loud music failed QA.** The renderer played tracks at their own loudness: 10 of 104 tracks sit above the accepted
   band (−20…−8 LUFS), so about one auto-picked track in ten lost its post after an AI designer had been paid for it.
   The per-track value is not enough (M43 is −11.7 LUFS as a track and −16.3 as the cut), so the exact segment is
   measured. Measured: the failed post (−7.2) renders at −14.3; the quietest track (needs a boost) lands at −14.1.
4. **The console could not say what became of a media post** (no final outcome in the run log). Now an explicit event.
5. **An invented claim.** See below.

## Frame-checked, not watched
Contact sheets of both reels show the full brand treatment (logo, Berlin pin, website, 5 s outro with the slogan and
"Order now"). Captions appear word by word, so a sampled frame can show half a caption. Weaker: the designer likes
abstract close-ups (a white rice frame in one reel).

## Needs your decision
1. **Process claims.** The pipeline produced the caption "we personally check every single Kacchi before it leaves our
   kitchen" and a picture reading "Checked. Confirmed.": the planner took "order accuracy" from a competitor gap, the writer
   invented the promise, and dk_intel's rules don't cover process claims. The designer's prompt now forbids them. For the
   writer and planner: add a process-claims rule to dk_intel (recommended; it is the one source of brand rules), or have
   you tell me what may be said about how the kitchen works.
2. **"Halal".** dk_intel blocks "halal" without a named certifier (§5 UWG). The reels notes and the 78-post campaign say
   "100% halal" is fine, and the designer is told to avoid the word. The two systems disagree.
3. **A draft key** (`dk api key-create content-agent --right draft`, in `.env` as `DK_PUBLISH_URL` and
   `DK_PUBLISH_KEY`) is the only thing between this and the first live `--publish dry-run`.
4. **Merging**: `feat/m8-reels-handover` here (unpushed: a push to `main` deploys) and a private GitHub repo for
   `dhaka-kacchi-reels` (it was not under git; the footage stays out of it).

## Not done
Nothing was sent to the real publisher. Cross-posting one reel to several platforms (the bridge supports it; the
orchestrator publishes each slot to its own platform). Non-English on-screen languages (the planner doesn't choose a
community yet; anything non-Latin is held until a native speaker has checked it). The template-writing loop (M9).
