# 0042. Keeping the disks from filling up: delete what nothing needs, and plan fewer days

- **Status:** Accepted
- **Date:** 2026-10-13

## Context
- On 2026-10-13 the VPS alerted `HostDiskLow` (13.65% free) and this Mac had 1.7 GB free of its disk. The studio makes about 12 MB per
  video and, over a day of comparison runs, left 152 job folders (1.9 GB) in the reels `out/jobs`, 49 knowledge packs (3 GB,
  one per build) and the 1.1 GB model bundle that had already been sent to the VPS.
- A 10-day plan keeps up to 150 posts' videos at once, on the Mac, on the VPS publisher (until each post is final plus 7 days) and
  as drafts waiting for approval.
- The owner's own footage and photos (about 11 GB) live in the same folders as the generated videos.

## Decision
- **A retention policy as data** (`config/studio/retention.yaml`) and one module (`studio/cleanup.py`) that plans first and
  deletes second. A generated video folder goes only when nothing needs it: its post is final and older than
  `keep_days_after_post` (3), or no post knows it (a test) and it is older than `unknown_keep_days` (1). It stays when its post
  is still waiting (any state that is not final, asked from the publisher for every state), when a stopped or cut-off run can
  still use it, or when a run is working. If the publisher cannot be asked, nothing is deleted. Old knowledge packs keep the
  active one and the newest 3; the upload copies keep the newest knowledge pack, and the model bundle (rebuilt by `pack ship
  --models`) is not kept.
- **Only two folders can be deleted from** (`out/jobs` and the pack and upload folders), as direct children, never a symlink:
  the delete function refuses anything else, so the owner's footage (`incoming/`, `raw/`, `public/`) and `out/campaign` are
  unreachable by construction.
- **Automatic and visible:** once a day inside the scheduled check (ADR 0039), a **Free disk space** button on Operations that
  first shows what it would free, `intel studio cleanup [--apply]`, and a Today card when under 15% is free.
- **A 5-day plan** (`horizon_days: 5`, `lead_days: 2`, `min_days_between: 3`): half the videos exist at once. Loading the settings
  refuses a `min_days_between` longer than `horizon_days - lead_days` (the calendar would go dry); a one-off `--days 1` is allowed.
- **The VPS** is reached only by commands the owner runs (rule 1): `docs/runbooks/disk.md`. The publisher already deletes media
  nightly (ADR 0021, 7 days after a post is final, `MEDIA_RETENTION_DAYS`); the runbook suggests 2.

## Consequences
- Good: the Mac stays tidy by itself; the run that frees space is a preview first; the owner's footage is unreachable; a shorter
  plan halves the videos kept at once and the tokens per run.
- Cost / risk: a deleted old pack cannot be rolled back to from this Mac (it can be rebuilt from its pull; the VPS keeps what it
  has); more, smaller runs (about every 3 days instead of every 6); a video deleted too early would need a re-render, hence the
  3-day grace and the "any state that is not final" rule.
- Not done: automatic cleanup on the VPS (no shell there); deleting old warehouse pulls and runs (small, and they carry the
  reproducibility of the models).

## Alternatives considered
- Deleting by age alone: would delete a draft the owner has not approved yet.
- A hard disk quota that refuses new runs: a stopped plan is worse than a tidy disk.
- Compressing old videos: slow, lossy, and they are not needed once posted.
