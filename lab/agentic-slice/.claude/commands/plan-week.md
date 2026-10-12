---
description: Orchestrate planner → 3 parallel writers → writer-lead for one platform (default instagram)
argument-hint: [instagram|facebook]
---
Platform: $ARGUMENTS (instagram if empty).

1. Delegate to the **planner** sub-agent: "Plan 3 posts for <platform>." It returns JSON with 3 slots.
2. Start **three writer sub-agents at the same time** (in one message), each with one slot's JSON as its task.
3. When all three are back, give their JSON results to the **writer-lead** sub-agent.
4. Report to the owner: a table (slot, day/time, first line, verdict, proposal id or why skipped), the sources,
   and how many agents and tool calls the run took. Then: "Proposals are in proposals.json, waiting for you."
