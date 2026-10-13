# 0035. Performance learning: growth-normalised results, plain statistics with intervals, a playbook the studio reads

- **Status:** Accepted
- **Date:** 2026-10-13

## Context
- The studio (ADR 0034) makes 130-150 posts per 10 days. Nothing yet tells it what works on each platform. The
  content scorer reads format and time, not words (P4.4), and has used up its test set (rule 9).
- The warehouse has 483 of our posts with metric snapshots (Facebook 255, Instagram 151, Threads 69, YouTube 8; TikTok
  none). A usual post has 1-2 likes, and the accounts grew a lot (Facebook reach about 5x since April), so raw counts
  compare a post with a different era. `tracked_link` is empty: nothing is attributed to orders.
- The publisher knows `post_key → live_url`; the warehouse knows `permalink → metrics`. The two write the same post
  differently (a reel is `/reel/<code>/` in one, `/p/<code>/` in the other).

## Decision
- **`src/intel/performance/`**, built by `uv run intel performance build` from the latest pull (0 tokens, read-only).
  One row per post with features known **before posting only** (rule 9): weekday, posting window (Berlin), format,
  caption length, first-line length, hashtags, emojis, a question, a call to action, German first line, two languages, a
  price.
- **Results are growth-normalised.** `lift` = (x + 1) / (median of the previous 20 matured posts on the same
  platform + 1), for reach (audience) and for engagement. A post is judged only after 7 days.
- **Plain statistics, not a model.** For each (platform, feature, value): the median lift, and the difference from the
  other posts with a 95% bootstrap interval (2,000 resamples, a fixed seed). "Works" or "hurts" only when the whole
  interval is on one side of zero and both groups have at least 15 posts; below 30 matured posts a platform gets no
  statement. The playbook states how many comparisons were made and how many findings may be chance by luck alone.
- **Findings are offered with their limits.** They overlap (reels also have short captions), they are reach and
  engagement, not orders, and anything a brand rule decides (language, prices) or the checker blocks anyway (a first line
  over 60, more than 5 hashtags) or the business weighs beyond reach (calls to action) is shown to the owner only,
  never to an agent as a lever.
- **The artifact** `data/performance/<pull id>/{playbook.json, playbook.md, posts.parquet, agent_posts.parquet}`
  (+ `LATEST`). The studio reads it from code and puts a few lines per platform into the strategist's and each
  adapter's task, or says "no playbook yet" / "no evidence": it never invents advice. The strategist writes a
  `playbook_note` for each idea that leans on a line, so a plan cites what worked.
- **Our own posts are joined to their results** (`intel performance join`): calendar `live_url` → platform post id →
  warehouse metrics, with what the studio knew about the idea (pillar, angle, hook length, shots, music).
- **Three read-only dk_intel tools** (`platform_playbook`, `performance_by_feature`, `post_performance`, server
  `dk_performance`, in the owner, analyst and planner profiles; the planner gets only the first) over the same files.
- **A weekly analyst agent** (`intel performance analyst`, one call, no tools, about 5k tokens) writes a plain "what
  worked" note and up to four proposals for the owner to approve. It may only use numbers in its task.
- No scorer is trained or promoted here (rule 6), and the content and reach test sets are not touched (rule 9).

## Consequences
- Good: every plan can say which past result it leans on; every line has its evidence beside it; the numbers say
  "nothing separates the posts" when that is true (Threads, YouTube, engagement on small counts).
- Cost / risk: ~50 comparisons per platform means a few false alarms; the playbook says so. Reach is not sales. Findings
  are correlational and overlapping.
- The playbook is built on the Mac. The dk_performance tools read the Mac's files; shipping the playbook to the VPS (so
  VPS-side agents can read it) joins the knowledge-pack mechanism later (S6).

## Alternatives considered
- Train a scorer on caption words: the test sets are spent (rule 9), counts are tiny and the label cannot see wording.
- A multivariate regression: with 100-250 posts per platform and overlapping features it would produce confident-looking
  coefficients that are mostly noise.
- Raw engagement counts: compares a post with a different-sized audience.
