# S5 results: a creative director, a template registry and footage knowledge (measured 2026-10-13, real model, real renders, real publisher dry run)

What was built: [ADR 0036](../adr/0036-creative-director-and-template-registry.md). Reels engine (private repo, merged to
its `main`): a template registry and 7 new templates; ingest and a curator profile for new footage.

## The toolbox (reels engine)
| Template | Formats | Variants | Used for |
|---|---|---|---|
| beat-reel | 9:16, 4:5 | default | footage cut to the music |
| kinetic-text | 9:16, 4:5, 1:1, 16:9 | stack, one-by-one, ribbon | text-first video over slow footage |
| photo-motion | 9:16, 4:5, 1:1, 16:9 | ken-burns, parallax, zoom-through | a slideshow video from photos (TikTok and YouTube from a carousel idea) |
| one-take | 9:16, 4:5, 1:1, 16:9 | clean, cinema, frame | one hero clip, lower third, closing line |
| photo, card, price, compare | carousel, album, story | default | existing slides |
| collage | carousel, album, story | grid, tilted, strip | 2-4 photos |
| steps | carousel, album, story | list, cards, path | a numbered how-to (text from the spec) |
| split | carousel, album, story | top, bottom, side | photo plus text |
| statement | carousel, album, story | bold, quote, badge | a big typographic card, 4 palette themes |

Reels' own checks after the merge: `tsc` clean, 39 of 39 tests, `npm run regress` passes (old stills bit-identical).
A render takes 11-15 s for a 9-14 s video; any slide about 1 s.

## What the studio does with it (4-day real runs, all five surfaces, the real publisher's dry run)
- 12 ideas planned and made, **48 of 48 posts valid** at the real publisher: 8 reels (5 platforms each), a carousel
  (Instagram, Facebook), an Instagram story (one picture, no caption), a single image (Instagram, Facebook, Threads) and a
  16:9 landscape video (YouTube, Facebook). 311k tokens (26k per idea), 7.5 minutes.
- **Variety:** 7 different templates across 12 ideas (kinetic-text 3, photo-motion 3, beat-reel 2, one-take, collage, steps,
  photo 1 each); no look twice in a row; variants change when a template repeats. Footage: 20 clip uses, 16 unique, 4 reused.
- **The director reasons like a director** (real answers): "a real family sharing one box deserves a single unhurried shot
  rather than cuts" (one-take), "a neighbourly handoff has a natural little sequence a beat reel can cut" (beat-reel), "the
  sealing and confirmation call is a promise, so bold ribboned text" (kinetic-text). It also chose a one-take for a spice-pour
  and a photo-motion zoom-through for a delivery sequence.
- I looked at a frame of every render (two contact sheets, sent to the owner). Typography, ribbon, framed-photo, cinema and
  story layouts do look different from each other.

## What the real runs found (and what changed)
1. **The scorer was dropping every carousel, story and image slot** (34-45%: it was trained on reels and photos, and the
   playbook says images reach less). The rotation would never have produced them. Now the scorer ranks the windows but only
   gates reels (`gate: false` per format in `routes.yaml`), and a copywriter's score-only "rewrite" is accepted for those
   surfaces (the words are still checked per platform).
2. **A caption fell under a photo frame** in the one-take "frame" variant: fixed in reels (the frame sits higher).
3. **An invented process claim got through again**: "every pot, sealed by hand" on a story. The brand rule for unverified
   claims now covers "sealed by hand" in German and English (with tests).
4. **A time-dependent bug:** the handover used the real clock, not the studio's, so one test failed after 08:58 UTC. Fixed.

## Footage curator (real, 3 clips)
`uv run intel studio curate --scope enrich --limit 3` took 23k tokens (7.8k each) and PROPOSED knowledge by looking at the
contact sheets: moments with start and end seconds, mood, energy, shot type, where text can sit, good-for. The answers named
what is on screen (a spatula plating rice, a hand reaching for a plated kacchi). Nothing was accepted: you approve with
`npm run curate -- --list` and `--accept-all` in the reels folder. 86 catalogued clips and photos still lack this knowledge
(about 700k tokens to enrich them all); music is analysed by code. The reels scan of your real folders found no new,
changed or removed files.

## Not done, and why
- **A template-writing agent.** Deliberately not built: an agent with a shell and write access should come after the
  registry has run for a few weeks. What exists: the director and designer log what no template could do
  (`uv run intel studio requests`, most asked first), and a person builds the template in the reels repo under its checks.
- beat-reel does not render 1:1 or 16:9 (the resolver says so clearly). A YouTube community post and a multi-picture Threads
  post do not exist in the publisher, so they are not templates.
- The curator has not enriched the existing 86 items; the renderer's new filters (mood, shot type, text space) have little to
  use until it does.

## Decisions for the owner
1. **Accept the curator's first 3 proposals?** In the reels folder: `npm run curate -- --list`, then `--accept-all`. Then say
   whether to enrich the other 83 (about 650k tokens).
2. **The default rotation** gives about 124 posts per 10 days (carousel 2, story 1, image 3, landscape 2 platforms on the third
   idea of each day) instead of 150 all-reels. Set `format_rotation: []` in `config/studio/plan.yaml` for all reels.
3. **Which new templates to build next** is the style-request list after a few weeks.
