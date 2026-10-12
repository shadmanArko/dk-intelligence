# 0032. Brand rules and prices have one owner: dk_intel; prompts and renderers hold none

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
- The Phase 5 plan ([PHASE5_CONTENT_STUDIO_PLAN.md](../PHASE5_CONTENT_STUDIO_PLAN.md), approved 2026-10-12) needs every
  part of the system to change in one place. The brand rules lived in three:
  - dk_intel's checks (`capabilities/checks.py`);
  - a "Never:" list in the lab designer's prompt;
  - the reels code: an agent price slide hard-coded three prices (€15.00, €9.99, €6.00).
- Those hard-coded prices were **not** part of the on-screen text the brand checks see, and `facts.yaml` marks the
  mutton Regular Box price and the Taster price as unconfirmed. An unconfirmed price could have shipped unchecked.
- A real lab run invented a process claim ("we personally check every Kacchi"), and no rule caught it.

## Decision
- **dk_intel is the only owner of brand rules and facts.** Rules version 1.1 adds two rules:
  - `UNVERIFIED_CLAIM`: checking or inspecting food, cooking durations or start times, "new in Berlin". Confirming
    an order personally is a fact (`ordering.confirmation`), so it stays allowed.
  - `PORTION_CLAIM`: how many people a box feeds.

  Both are blocks, with failing and passing examples in the tests. The grounding pack's brand reminders state them.
- **"Halal":** unchanged. Blocked without a named certifier (`claims_status.halal.certified`), as the owner decided
  on 2026-10-12.
- **Prompts hold no rule lists.** The designer follows the `brand` list in its task, which comes from dk_intel, and
  every on-screen word is checked before anything renders.
- **Renderers hold no prices.** A reels price slide shows only the `items` the spec gives, and every item is on-screen
  text the checks see. A price slide without items is refused. The lab fills items in code from `get_facts`, only for
  menu items whose name, price **and** availability are confirmed. With none, the slide becomes a card. The owner's
  hand-made campaign slides keep their own rows.

## Consequences
- Changing a rule or a price means editing `facts.yaml` or the checks once; the prompts and the renderer follow.
- Today only Shahi Borhani (€6.00) qualifies for a price slide, until the owner confirms the mutton box price and
  availability in `facts.yaml`.
- The hand-made campaign (78 posts, already published) still says "100% halal" on a few slides. It is the owner's
  content and is left alone, but it contradicts this rule.
