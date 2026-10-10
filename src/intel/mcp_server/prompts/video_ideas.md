---
name: video_ideas
version: 1.0.0
description: Short-video ideas with a shot list, a two-second hook and an audio rule, for one platform
args: [platform]
tools_used: [brand_rules, upcoming_occasions, search_posts]
max_tool_calls: 4
---
Give 3 short-video ideas for {platform} for Dhaka Kacchi Berlin.

1. brand_rules(platform="{platform}") and upcoming_occasions(days=14, platform="{platform}").
2. At most one search_posts(source="all", sort="performance") for videos that worked.
3. For each idea: the hook in the first 2 seconds, a shot list (5 to 8 shots with seconds), script lines,
   on-screen text (German first), the audio rule (our original sound or the platform's own library, never
   unlicensed music), and the cites it rests on. Real footage of our food only.

{rules}
Budget: at most 4 tool calls.
