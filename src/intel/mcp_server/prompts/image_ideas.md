---
name: image_ideas
version: 1.0.0
description: Shot directions for real photos (never AI images of the food) for one platform
args: [platform]
tools_used: [brand_rules, upcoming_occasions, search_posts]
max_tool_calls: 4
---
Give 5 photo ideas for {platform} for Dhaka Kacchi Berlin. Real photos only: AI images of the food would mislead
customers, so never suggest generating them.

1. brand_rules(platform="{platform}") and upcoming_occasions(days=14, platform="{platform}").
2. At most one search_posts(source="own", sort="performance") for photos that worked (from their captions).
3. For each idea: subject, composition (angle, distance, light), props, text overlay of at most 3 words,
   alt text, which occasion or angle it serves, and the cites it rests on.

{rules}
Budget: at most 4 tool calls.
