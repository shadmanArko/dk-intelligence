# 0036. A creative director chooses the look; code enforces variety; the engine owns the templates; new footage becomes knowledge

- **Status:** Accepted
- **Date:** 2026-10-13

## Context
- 150 posts per 10 days looked repeated: one video template (footage cut to the beat) and four slide templates, the same
  format for every post. The owner wants the toolbox of a head of creative: templates native to each platform (reels,
  carousels, stories, landscape video, text-first, slideshows), no repetition, and a system that reads new footage, photos
  and music and uses what it learns.
- The publisher accepts: Instagram reel, feed, carousel and story; Facebook video, album and text; Threads text, one image,
  video; TikTok video; YouTube video. Not accepted: a YouTube community post, a multi-picture Threads post.
- Agents must never write TypeScript per post (ADR 0031); brand rules and facts have one owner (ADR 0032).

## Decision
- **Templates belong to the reels engine** (a registry, one folder per template with `meta.json`: family, formats, platforms,
  duration, music use, what it needs, variants, a `designer_brief` and an example spec). `list_templates` reports it as
  `template_registry`. The studio reads it; it holds no template knowledge itself.
- **A creative-director agent** (`studio/director.py`, `agents/director.md`, no tools) chooses each idea's template, variant,
  music hint and pace, one call per day so the day's three ideas contrast. It reads `config/studio/craft.yaml`: how a senior
  director makes each kind of post on each platform (craft heuristics the owner can edit; not brand rules, not platform
  limits).
- **Code enforces variety**: the template must fit the format, must differ from the post before it, may not exceed
  `max_template_share` of the plan, and a repeated template changes variant. If the director fails, code picks a valid, varied
  look. A small ledger remembers the last posts' looks across runs.
- **More surfaces, by rotation** (`plan.yaml` `format_rotation`): reels for ideas 1 and 2 (reach on all five platforms), idea 3
  rotates carousel, story, image, landscape. New route formats `story` (Instagram only, no caption, so no copywriter or
  adapter is paid for) and `landscape` (16:9 for YouTube and Facebook). An idea whose format does not reach Instagram is
  written and timed by its first platform (`Idea.lead`).
- **The designer fills the director's template**: its task carries the template, variant, the registry's brief and the example
  spec. Code owns size, template and variant in the spec; the designer owns the content.
- **A footage curator** (`studio/curator.py`, `agents/curator.md`): the reels engine's ingest finds new, changed and removed
  files and makes facts and contact sheets; the curator agent looks at a sheet (the built-in Read tool on exactly that
  picture) and proposes knowledge (moments, mood, energy, shot type, where text can sit); the engine validates it against
  its vocabulary and writes a pending file; **a person accepts** (`npm run curate`). The renderer's search uses the knowledge.
  Removed files are retired, never deleted from knowledge.
- **Style requests**: when no template fits, the director and designer say so in a sentence; `intel studio requests` counts
  them. A person builds the missing template in the reels repo under its checks. An agent that writes template code on its own
  is deliberately not built (an agent with a shell and write access needs the registry to settle first).

## Consequences
- Good: variety is a rule in code, not a hope in a prompt; each platform gets craft in its own words; the owner can change a
  line of craft or the rotation without a developer; new media is picked up without hand-writing.
- Cost / risk: one more AI call per day (about 5k tokens); the rotation lowers the post count (carousel 2, story 1, image 3,
  landscape 2 platforms instead of 5): about 124 posts per 10 days instead of 150 with the default rotation.
- The curator proposes; nothing becomes knowledge until the owner accepts.

## Alternatives considered
- The director also choosing the format: the scorer picks windows per format, and the owner's rotation is clearer to reason
  about; the director chooses within a format.
- Many masters per idea (a reel, a carousel and a story from one idea): three times the renders and the review load; one
  master per idea stays, with the rotation giving the variety.
- Templates in the studio repo: the engine owns rendering (ADR 0032); the contract is the registry.
