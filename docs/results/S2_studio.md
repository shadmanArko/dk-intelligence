# S2 results: the studio at full scale (measured 2026-10-12, real model, real renders, real publisher)

What was built: [ADR 0034](../adr/0034-the-studio-package.md), [STUDIO.md](../STUDIO.md). What was run: the real
`claude` agents, the real reels renderer, the real dk-publishing API in **dry-run** mode (every post validated by the
publisher; nothing created in the owner's Calendar). Inputs for the three 10-day runs were frozen once
(`data/studio/eval/inputs-10day.json`: the grounding pack, the scorer's answers, prices) so the runs are comparable.

## The headline
One command planned 10 days, wrote and designed 26 ideas, rendered 26 masters and had **128 platform posts validated by
the real publisher**, with every platform text and every on-screen word checked by dk_intel:

| | v1 (first real run) | v2 (limits and menu in the task) | v3 (+ retry, German check, designer repair) |
|---|---|---|---|
| Ideas made | 22 of 26 | 20 of 26 | **26 of 26** |
| Posts valid at the publisher | 96 of 130 | 94 of 130 | **128 of 130** |
| Tokens (cap 4,000,000) | 634,421 | 531,983 | 541,475 |
| Tokens per idea / per post | 24,401 / 6,609 | 20,461 / 5,660 | **20,826 / 4,230** |
| Time | 748 s | 661 s | 836 s |
| Held, by reason | text 26, on-screen 1, spec 1 | text 18, agent 4 | text 2 |

Not 30 ideas and 150 posts: **4 of the 30 slots were ruled out by the content scorer** (the second idea of a Thursday,
Friday and Monday scores 51-52% in its last free window, below the bar; all three ideas of a day need all three windows).
That is the calendar telling us something, and it is a decision for the owner (see "Decisions"). With the four slots
filled the same run would be about 150.

A smaller real run (1 day, 3 ideas): 15 of 15 posts valid, 80,500 tokens, 171 s.

## Where the tokens and the minutes go (v3)
| Step | Calls | Tokens | Share |
|---|---|---|---|
| Strategist (3 chunks of ~10 ideas) | 3 | 22,042 | 4% |
| Copywriter (26 ideas, 2 retried) | 28 | 161,743 | 30% |
| Platform adapter (5 texts per call; 6 repair rounds) | 32 | 90,026 | 17% |
| Designer | 26 | 267,664 | 49% |
| Everything else (checks, renders, uploads, handover) | code | 0 | 0% |

- Time: 94 s planning angles, then 735 s for production in which three ideas are written at a time while renders and
  uploads run behind them; the handover itself takes 2 s. 26 renders took 21.5 s on average (max 41 s), one at a time;
  each upload took 26 s on average and overlapped the next render. At this rate a 30-idea run is about 16 minutes
  and 0.62M tokens, 15% of the budget.
- The designer is half of the cost. That is where a cheaper model would be measured first (S6, D11).

## What the real runs found (and what changed)
The fake-model tests passed all the time; these came from reading the real traces:
1. **Platform limits were in nobody's task.** v1 held 26 texts (`FIRST_LINE_LONG`, `HASHTAGS`, a YouTube description with
   a long first line). The adapter now gets each platform's hard limits from code (first line, emojis, hashtag range,
   publisher caption limit). v2: none of those.
2. **The brand rule worked and the agents didn't know.** The strategist planned a video about the lamb box; the new
   `UNAVAILABLE_ITEM` rule (rules 1.2, from facts.yaml) blocked its on-screen words in v1. The agents now get
   `not_on_the_menu` from the same data. v2 and v3: no hit.
3. **The German-first-line check was too narrow.** 5 of 26 ideas lost every platform in v2 because "Festliche Kacchi in
   Berlin" and "Samstagabend in Reinickendorf" were not recognised as German; the check now knows common endings and
   words, with tests (an English line such as "Young Kacchi in Berlin" still fails).
4. **One agent answer in about 40 was unusable** (no JSON, a broken string). Asked once more, never more.
5. **A refused spec** ("no video in category Plated kacchi with rating 2 or more") now goes back to the designer once
   with the renderer's own words; v3 needed it 0 times, and a test covers it.
6. **The model said "keep" for a caption the checker then failed.** Every platform text is re-checked by code; nothing
   the agent claims is trusted.

## Findings that need the next milestone (S4)
- **The five texts of an idea are close to each other.** The adapter obeys the limits but mostly rephrases one message.
  What would make them native is knowledge of what performs on each platform (the S4 playbook); the `feel` lines in
  `config/studio/routes.yaml` are only a start.
- **Quality is checked by rules and by the scorer, not measured.** 1 of 26 ideas has an Instagram score below the bar
  (words fine, slot weak); no signal yet says which caption is better (playbook N1).

## Tests and gates
- 37 new tests (`tests/test_studio.py`, `test_studio_mcp.py`): settings refused on a typo; real dates across the clock
  change; windows chosen by the scorer; 150 posts through every step with no call over 20 drafts; budget stop; resume
  after a crash with 0 repeated calls; one repair round; retry once; failed agent, render and platform each hold only
  themselves; carousels reach 2 platforms and one image 3; every spec valid against the pinned contract; the adapter's
  limits come from the publisher; the console; the MCP hub against a real stdio server.
- dk_intel brand tests gained a failing and a passing example for `UNAVAILABLE_ITEM` and the German endings.
- `ruff`, `mypy --strict` for the whole `intel.studio` package, and `pre-commit` pass.

## Decisions for the owner
1. **The four ruled-out slots.** Options: leave 26 ideas (about 130 posts); make one idea on Monday, Thursday and Friday a
   carousel or an image (they pass, but reach 2-3 platforms); or add a fourth window (a posting hour the owner picks).
   Recommended: change `format_mix` for those days only after S4 shows what works; until then 26 ideas is fine.
2. **TikTok `only_me`** stays until the owner trusts the output.
3. **The first real 10-day drafts run** (`--publish drafts`) creates about 130 drafts in the Calendar. It waits for the
   review page to be live (S3 deploy), so that approving 13 posts a day takes minutes.
