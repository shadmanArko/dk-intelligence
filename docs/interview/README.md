# Interview kit

Everything needed to present this project in an ML-engineering leadership interview. All numbers come from the
repository: the decision records, the results and the tests.

| File | Use it for | Prep time |
|---|---|---|
| [ONE_PAGER.md](ONE_PAGER.md) | the system on one page; print it or share it; talk from it | read 5 min |
| [TALK_TRACKS.md](TALK_TRACKS.md) | the 2-minute and 10-minute versions, with a rehearsal checklist | rehearse 30 min |
| [STORIES.md](STORIES.md) | one STAR story per milestone (H0–H9), each with its trade-off and follow-ups | pick 3 |
| [INCIDENTS.md](INCIDENTS.md) | six real incidents in STAR form, and which one answers which question | pick 2 |
| [TRADEOFFS.md](TRADEOFFS.md) | 18 decisions with "would change when", what I left out, the 10× team / 100× data answer | read once |
| [QUESTIONS.md](QUESTIONS.md) | likely questions with short answers, including the sceptical ones | drill 20 min |

## Before an interview (45 minutes)
1. Read the one-pager. Say the 2-minute version out loud, timed.
2. Pick three stories for this company: one technical (H4 or H6), one judgement (H9 or TRADEOFFS #14), one failure
   (INCIDENTS 1 or 4).
3. Drill the sceptical questions in QUESTIONS.md, especially "over-engineered?", "75 posts?" and "did you write it
   yourself?".
4. Have a link ready: ADRs (`docs/adr/`), one runbook, one postmortem. Showing a real artefact beats describing it.

## Ready when
You can give the 10-minute version from the one-pager alone, and answer "what would make you change your mind?" for
any row of TRADEOFFS.md.

H10 (data protection and responsible AI) is built and tested on the `feat/GDPR` branch, with the pull request open.
Mention it as "built, merge pending" until it's merged. Its story is added to STORIES.md by that branch.
