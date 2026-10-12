---
description: Orchestrate planner → 3 parallel writers → writer-lead for one platform (default instagram)
argument-hint: [instagram|facebook]
---
Platform: $ARGUMENTS (instagram if empty).

1. Call `list_slots` (demo_publisher) and `grounding_pack` (dk_intel_planner, days 7) yourself, in one message, and
   give both as JSON to the **planner** sub-agent. It returns JSON with 3 slots.
2. Start **three writer sub-agents at the same time** (in one message). Each gets one slot's JSON plus `evidence`
   (the pack's quotes and posts for that slot's sources) and `examples` (2 of the pack's `our_best_posts`).
3. When all three are back, give their JSON results to the **writer-lead** sub-agent.
4. Report to the owner: a table (slot, day/time, first line, verdict, proposal id or why skipped), the sources,
   and how many agents and tool calls the run took. Then: "Proposals are in proposals.json, waiting for you."
