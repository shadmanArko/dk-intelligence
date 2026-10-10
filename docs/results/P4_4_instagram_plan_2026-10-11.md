# A 14-day Instagram plan made through dk_intel (11–24 Oct 2026)

Made in Claude Code with the `content_plan` workflow: 1 `grounding_pack`, 1 `search_posts`, 3 `evaluate_drafts`
(plus `brand_rules` and `get_facts` for the cadence and the confirmed facts). Every price and claim below is
`confirmed` in `get_facts`; nothing `unconfirmed` (taster box price, order deadline, pickup place, "halal") is used.
A plan for the owner to edit, not to post blindly.

## Grounding used
- **Occasions:** Indian festive season from 18 Oct (celebratory, sales allowed); **respectful days 13 and 23 Oct:
  quiet posts, no sales wording** (`occasion_tone: solemn`).
- **Times:** best weekdays Fri, Sat, Tue; reels in the playbook's pre-dinner window (16:30–17:30).
- **Competitor gaps** (weakness miner + a cited review): delivery time, e.g. promised 15–40 minutes, then 60
  [R-B2BXH9A3]; taste [R-HEF8TGWN]; wrong or missing items [R-TGREXQ8Q]. Our angle: one cooking day, Saturday slots,
  a personal message before delivery.
- **Our best posts:** slow-dum mutton kacchi, 3.5× our typical engagement [P-B4SE3BWP]; "slow-cooked on dum for 2
  hours", 2.5× [P-H02P4V2A].
- **Opportunities (patterns to test):** rice, where competitors get 50% negative mentions and our rice posts did up
  to 5.7× [P-ZX127HF5]; meat, 45% negative, and our "Mutton just walked in" post did 19.9× [P-7Z0Q0J7B].

## The plan (all `keep` after the loop)
| Day | Time | Format | First line | Angle and sources | Score |
|---|---|---|---|---|---|
| Sun 11 | 17:00 | reel | Der Topf bleibt zu, bis die Kacchi fertig ist. | sealed-pot craft [P-H02P4V2A] | 0.58 |
| Mon 12 | 17:00 | reel | Samstag ist Kacchi-Tag in Berlin 🍛 | delivery gap: one cooking day, personal message [R-B2BXH9A3] | 0.57 |
| Tue 13 | 17:00 | reel | Heute ein ruhiger Tag in unserer Küche in Berlin. | respectful day: no sales | 0.62 |
| Wed 14 | 17:00 | reel | So soll Kacchi-Reis aussehen: locker und duftend. | rice opportunity [P-ZX127HF5] | 0.60 |
| Thu 15 | 17:00 | reel | Kacchi ist kein Biryani. Hier ist der Unterschied 👀 | explainer (was a carousel, see below) | 0.56 |
| Fri 16 | 17:00 | reel | Bestellungen für Samstag sind offen! | order call: Smokey Lamb Kacchi 1000 ml, 15 € (confirmed) | 0.55 |
| Sat 17 | 17:00 | reel | Samstag in Berlin: der Deckel geht auf 🔥 | dum reveal [P-B4SE3BWP] | 0.64 |
| Sun 18 | 17:00 | reel | Festtage in Berlin: Reis und Fleisch auf den Tisch. | festive season, festive tone, no greeting copied | 0.59 |
| Mon 19 | 17:00 | reel | Shahi Borhani: Joghurt, Minze, Gewürze 🥛 | Borhani, 500 ml, 6 € (confirmed) | 0.57 |
| Tue 20 | 17:00 | reel | Der Hammel braucht eine Nacht, die Kacchi 24 Stunden. | meat opportunity [P-7Z0Q0J7B] (was a carousel) | 0.60 |
| Wed 21 | 17:00 | reel | Ein Gast kam für die Borhani zurück. | customer moment | 0.60 |
| Thu 22 | 17:00 | reel | Kacchi für euer Team-Event in Berlin? | catering on request (confirmed) | 0.57 |
| Fri 23 | 17:00 | reel | Heute sagen wir einfach danke, Berlin. | respectful day: no sales | 0.57 |
| Sat 24 | 17:00 | reel | Kacchi-Samstag: frisch aus dem Topf in Berlin. | Saturday: delivery across Berlin or free pickup | 0.64 |

Every caption: German first line (≤60 characters), one English line, 3–4 core hashtags (#kacchibiryani
#berlinfood #biryaniberlin #foodberlin), at most one emoji in the first line.

## The loop, measured
| Round | Drafts | keep | rewrite | blocked | Mean content-scorer probability |
|---|---|---|---|---|---|
| 1 (first drafts) | 14 | 12 | 2 (both carousels at 11:30) | 0 | **0.565** |
| 2 (sharper carousel hooks) | 2 | 0 | 2 | 0 | carousels 0.394 → 0.382, 0.436 → 0.423 |
| 3 (same angles as reels at 17:00) | 2 | 2 | 0 | 0 | — |
| **Final** | 14 | **14** | 0 | 0 | **0.589** (+0.024) |

- **100% of kept captions pass the brand checks** (no errors, no warnings).
- **What moved the score was format and time, not wording.** The scorer reacts strongly to content type and posting
  window; rewriting the carousel captions did nothing. Turning them into reels did. That is a real signal (our
  past carousels underperformed), but it is also exactly how a score can be gamed. The guards (ADR 0028): at most 2
  rewrite rounds, the scorer only advises, and the shadow ledger keeps measuring what posted captions really do.
- **A checker bug found by doing:** "Warum Hammel? Weil Kacchi Zeit braucht." was flagged as not German first; the
  German detector now knows common words like *warum, weil, ist, ein* (fixed, with a test).
- **Cost:** 6 tool calls for the whole plan; `grounding_pack` ≈1.3k tokens.
