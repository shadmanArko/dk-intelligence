---
name: content_plan
version: 1.0.0
description: A cited posting plan for one platform, every caption checked and scored, with a bounded rewrite loop
args: [platform, days]
tools_used: [grounding_pack, search_posts, evaluate_drafts]
max_tool_calls: 12
---
Make a {days}-day posting plan for {platform} for Dhaka Kacchi Berlin.

1. Call grounding_pack(platform="{platform}", days={days}) once. Use its occasions (respect sales_ok=false and the
   respectful days), posting_times, competitor_gaps, our_best_posts, what_works, opportunities and brand_reminders.
2. At most one search_posts(source="own", sort="performance", platform="{platform}") for hooks that worked.
3. Plan the cadence from brand_reminders and the platform rules. For each post write a draft:
   date, time (Europe/Berlin), format, pillar, idea, first_line, caption (German first, then one English line;
   Bengali only for community posts), hashtags, alt_text, cta, and sources (the cites you used).
4. Call evaluate_drafts in batches of at most 20 (platform="{platform}", the format, content_type, and
   planned_posted_at with a timezone such as 2026-10-14T18:30:00+02:00, so Facebook and Instagram drafts get scored).
5. Rewrite every draft with verdict "rewrite" using its fixes, drop every "blocked" one, and evaluate again.
   At most 2 rewrite rounds: the scorer is a guide, not the goal.
6. Answer with a table (date, time, format, first line, verdict, score) and then each post in full with its
   sources. End with: drafts written, kept, rewritten, dropped, and the mean score before and after rewriting.

{rules}
Budget: at most 12 tool calls.
