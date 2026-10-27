# 0043. Add LinkedIn as the sixth platform, handed to the owner on Telegram

- **Status:** Accepted
- **Date:** 2026-10-27

## Context
The owner wants LinkedIn posts in a formal company voice. Posting to a company Page through LinkedIn's API needs the
Community Management API: a registered organisation, a verified Page admin and a review that takes weeks and may be
refused. dk-publishing already has a mode for this case: `assisted` (ADR 0019 there, used for TikTok), where the post
arrives as a Telegram card with the caption to copy and the files attached.

On the studio side LinkedIn was already known to the brand tools (caption limit 3000, playbook format `multi_image`,
`LINKEDIN_LINK` rule, no occasions) but no route reached it, and two defaults fought a formal LinkedIn text: the first
line limit (60 characters) and the two-line caption shape in the adapter prompt.

## Decision
1. **LinkedIn is `assisted` in dk-publishing** (formats `text`, `image` 1-9 pictures, `video`; caption up to 3000;
   option `post_as` page or profile). The studio targets it with its slide formats: `carousel` and `image` get
   `linkedin: image`. Reels are not sent to LinkedIn, so a reel run is still 150 posts per 10 days.
2. **Platform differences are data, not prompt text:** `routes.yaml` gains `feel.linkedin` (formal, Sie or neutral,
   no hype) and a new `shape:` map the adapter follows instead of the default shape (`caption_shape` in its task);
   `brand.py` gets per-platform first-line and emoji limits (LinkedIn 140 and 2; every other platform unchanged).
3. **Still judged on checks only:** LinkedIn has no content-scorer model and no posting-times model, so it never gates
   a slot; the primary platform stays Instagram. No LinkedIn performance data exists yet, so no LinkedIn evidence is
   claimed.
4. **The API is a later swap, not a rewrite:** if LinkedIn grants the Community Management API, only the publisher's
   adapter and its mode change.

## Consequences
- Good: LinkedIn content flows through the same agents, checks, drafts and approval as the other platforms, with no
  LinkedIn API approval needed; the owner can post as the Page or from a profile.
- Good: the per-platform shape/limits are reusable for the next platform.
- Cost / risk: the owner posts by hand (a Telegram card), and nothing can confirm that the post went out.
- Cost / risk: the adapter prompt now has a conditional rule; a wrong `shape` text would weaken a LinkedIn caption
  only, never the other platforms.
- Open for the owner: the tone rule in `brand/voice-and-rules.md` is still marked `[CONFIRM]` (Sie versus du), and
  whether the Page or the founder's profile posts. The shipped defaults are Sie or neutral and the Page.

## Alternatives considered
- Wait for the Community Management API before doing anything: weeks of delay for a platform the owner wants now.
- Post as a personal profile through the self-serve `w_member_social` scope: it is not the company Page, needs OAuth
  token upkeep, and the owner would still want a look at each post.
- Make LinkedIn a reel target too: it would raise the daily post count to 180 and send video where the playbook says
  pictures work.
