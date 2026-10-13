# S4 results: the learning loop (measured 2026-10-13, real warehouse pull, real model, real publisher dry run)

What was built: [ADR 0035](../adr/0035-performance-learning.md), [STUDIO.md](../STUDIO.md) ("What worked"). Data: the
warehouse pull of 2026-10-11: 483 of our posts with 8,775 metric snapshots (Facebook 255, Instagram 151, Threads 69,
YouTube 8, TikTok 0).

## What the numbers say (`uv run intel performance build`, 0 tokens, 0.1 s)
Results are compared with the previous 20 posts on the same platform, so a growing account is not mistaken for better
posts. About 50 comparisons per platform; the playbook states that 2-3 findings may be chance.

| Platform | Matured posts | What the data supports (reach; 95% intervals) |
|---|---|---|
| Instagram | 138 | **Reels reach 2.1x** a usual post (1.5-3.6x) and images 0.6x. **No emoji 2.4x** (1.7-6.0x), 3 or more emojis 0.8x. **Short captions (under 100 characters) 2.1x**, long ones (250+) 0.8x. Monday weaker (0.7x, 22 posts, an indication). |
| Facebook | 245 | Video 1.3x (1.06-1.5x) and images 1.0x; Tuesday 1.3x (29 posts, an indication). Questions slightly weaker. |
| Threads | 62 | Nothing: the warehouse has no reach or views for Threads (every value is 0), so no result varies. Said as "no evidence". |
| YouTube | 3 | Too few matured posts: no claim. |
| TikTok | 0 | No data (posted by hand, no analytics). |

Engagement (likes, comments, shares, saves) separates nothing clearly: a usual post gets 1-2 of them, which is too few
to compare. Reach is the usable result. **Reach is not orders**: `tracked_link` is empty, so nothing is tied to sales,
and the playbook says so on every page.

Findings that conflict with brand rules (two languages, calls to action) are shown to the owner only. Findings the checker
blocks anyway (a first line over 60 characters, more than 5 hashtags) are not offered as levers.

## Does it change what the studio makes? (same frozen 10-day inputs, real runs)
| | v3 (no playbook) | v4 (playbook) | v5 (playbook + wider German check) |
|---|---|---|---|
| Ideas made | 26 of 26 | 24 of 26 | 26 of 26 |
| Posts valid at the publisher | 128 of 130 | 115 of 130 | 126 of 130 |
| Tokens (per idea) | 541k (20.8k) | 532k (20.4k) | 608k (23.4k) |
| Time | 14 min | 11.5 min | 13 min |
| Instagram captions with no emoji | 38% | 74% | 84% |
| Ideas that cite what worked | 0 | 10 of 26 | 26 of 26 |

- **Behaviour changed, measurably:** with the playbook in the task, Instagram captions went from 38% to 84% without an
  emoji, the thing the data says reaches 2.4x. Every idea in v5 names the line it leans on (`playbook_note`).
- **Cost:** about +2.5k tokens per idea (the playbook lines in 3 strategist calls and every adapter call).
- **The v4 dip was the checker, not the playbook:** 11 platform texts failed `LANG_ORDER` because plainly German first
  lines ("Kacchi unterwegs durch Humboldthain", "Nachbarn teilen Kacchi in Berlin") were not recognised as German by a
  70-word list. The list was widened (with tests; English lines such as "Quiet moment before the pot" still fail) and
  v5 recovered to 126 of 130; one idea still lost four platforms to the same heuristic.
- **Honest limit:** the strategist's notes mostly say "reel format reaches 2.1x", but the format and the window are decided
  by code (`format_mix`, `slots.yaml`), not by the AI. The levers an agent controls are the caption (emojis, length) and the
  picture; the format and day levers belong to the owner (below).
- Not measured here, because nothing has run long enough: whether the studio's own posts beat the older ones. The first
  join (`intel performance join`) finds 0 published studio posts today; it needs a week after the first approved drafts.

## The weekly analyst (one real call, 5.4k tokens)
`uv run intel performance analyst` wrote a plain note from the playbook only: what worked (reels, no emoji, short
captions, Facebook video), what did not, what is too little data (TikTok, YouTube, our own posts), and 4 proposals with
the evidence and what would show each wrong. It cited only numbers in its task. (Its headline was clumsy; the proposals
were sound.)

## Tests and gates
- `tests/test_performance.py` (19), `test_mcp_performance.py` (5) and 2 new studio tests: one key for one post across
  platform address styles; the Berlin window across the clock change; growth-normalised lift; a planted effect is found
  and noise and small groups are not; rule-bound lines go to the owner; the join of a reel (`/reel/` vs `/p/`);
  the three tools and their profiles; the strategist and adapter carry the lines; "no playbook" is stated as such.
- The data contracts for `social_post` and `social_metrics_snapshot` now include `permalink`, `saves` and `views`
  (the pull validates them), and `contracts/registry.yaml` lists the performance join. dk_intel gained 3 tools (the
  reviewed snapshots only grew).
- `ruff`, `mypy --strict` for `intel.performance`, `pre-commit`.

## Decisions for the owner
1. **Let the playbook choose the format and the window?** Today code fixes the mix (all reels) and the windows. The data
   says reels and no-emoji captions reach more on Instagram and video on Facebook; it says nothing yet about orders.
   Recommended: keep all reels (the data agrees), and decide windows after the first weeks of the studio's own results.
2. **Threads and YouTube data:** Threads has no reach in the warehouse and YouTube 3 matured posts. Until the harness
   collects Threads views, nothing can be learned there.
3. **Order attribution:** `tracked_link` is empty. Tracked links on the studio's posts (the link builder) are what would
   turn reach into orders.
