---
name: caption_ideas
version: 1.0.0
description: Several caption options for one platform, ranked by checks and the scorer
args: [platform, count]
tools_used: [grounding_pack, search_posts, evaluate_drafts]
max_tool_calls: 6
---
Write {count} caption ideas for {platform} for Dhaka Kacchi Berlin.

1. Call grounding_pack(platform="{platform}", days=14) once.
2. At most one search_posts(source="all", sort="performance") on the angle you pick, for hooks that worked.
3. Write {count} different captions: each with a different angle (taste and craft, a competitor gap we solve, an
   occasion, behind the scenes, a customer moment). German first line, then one English line.
4. evaluate_drafts on all of them (planned_posted_at with a timezone for Facebook and Instagram). Rewrite each
   "rewrite" once with its fixes; drop "blocked".
5. Answer ranked best first: the caption, its angle, verdict, score, and the cites it rests on.

{rules}
Budget: at most 6 tool calls.
