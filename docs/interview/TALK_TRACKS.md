# Talk tracks

Both are spoken scripts: read them aloud, then put them away and talk from [ONE_PAGER.md](ONE_PAGER.md). Rule of thumb:
**one number per claim, one trade-off per section.** Stop after each section and look for a question; the
interviewer's question is worth more than your next paragraph.

---

## The 2-minute version (≈ 300 words)

> I built the ML platform for a small Berlin food business: a biryani kitchen run by one person. There are seven
> models. Two scorers predict whether a draft post will beat our usual post and reach more people. Five insight
> models cover competitor weaknesses, which Berlin areas to target, an occasion calendar, the website funnel and
> creator matches.
>
> The interesting part isn't the models, it's the system around them. Every Sunday the laptop snapshots the data,
> checks it against data contracts, trains, and runs a **judge**. A challenger has to beat the current champion on
> the newest posts, with a paired bootstrap confidence interval, and on no platform may it get worse. If it passes,
> it's sealed into a package with its feature code and model card and dropped into an upload-only inbox on the VPS.
> The owner approves it on Telegram.
>
> Once it's live, every post is scored *before* its outcome exists, in a shadow ledger. So I always have honest
> evidence. Monitoring checks drift and live accuracy hourly. If a production model becomes significantly worse
> than the system it replaced, by a McNemar test on the same posts, a guardrail rolls it back automatically. But
> nothing automatic can ever *promote* a model. Only the judge plus a human can.
>
> Around that sit the production basics:
> - build once and deploy by digest, with automatic rollback;
> - SLOs with burn-rate alerts and a dead-man's switch;
> - a hash-chained audit trail;
> - a weekly restore drill;
> - the host as Ansible code;
> - a measured capacity plan.
>
> The cost is zero euros a month in ML infrastructure: one shared VPS, one laptop.
>
> The judgement I'm proudest of is what I *didn't* build. No Kubernetes and no feature store, because one box at
> 50,000× below capacity doesn't need them. Each one has a written trigger in the scaling plan for when it would.

**If they want one story:** H6, the guardrail ("can withdraw, never promote"). **If they want one number:** the
production model reproduces bit-exactly, 75 of 75 predictions.

---

## The 10-minute version

Timing in brackets. Each section ends with the question you're inviting.

### 1. The problem and the constraint (1 min)
- A one-person kitchen. Decisions: what to post, when, where to deliver, which occasions, which creators.
- **The constraint shapes everything:** one owner as the only operator and on-call, one shared 8 GB VPS that also
  runs the ordering system, no budget, and a few hundred posts of history.
- So the goal wasn't "the best model". It was **a decision system you can trust when nobody's watching.**
- *Invite:* "Happy to go into the models or the platform. Which is more useful?"

### 2. The data (1 min)
- **Three repositories:**
  - the business warehouse (orders, posts, metrics, web events);
  - a market-data platform I also built (1.87M public posts, competitors, occasions, census-style area statistics);
  - this ML repo.
- **Snapshots are immutable and checksummed**, and the model's name carries the data fingerprint.
- **Data contracts** (pandera) on all 17 input tables. Story: the first real run caught a statistics table mixing
  countries with subtotals, which inflated an audience 3.6×. The fix went upstream, to the data's owner.
- *Trade-off:* the contract encodes what the consumer relies on, not "every row unique".

### 3. Models and the judge (2 min)
- **Honest small-data ML.**
  - The test set is the 75 newest posts, never shuffled.
  - The champion is the harness's existing predictor, which I first reproduced exactly.
  - The challenger needs a gain *and* no platform worse *and* calibration not worse.
- **A failure I kept:** the first content scorer *failed* (0.623 vs 0.625), and a second attempt overfit
  cross-validation (0.608 CV → 0.560 test). I stopped tuning against that test set because it had been seen twice.
  The model that later passed did so on new data: **0.688 vs 0.631**, 95% CI 0.60–0.78.
- **A negative result I kept:** a TikTok-trained "market prior" scored AUC 0.528 on newer posts. Caption features
  don't transfer across platforms, so it was left out.
- *Invite:* "Ask me about n = 75 and confidence intervals." (Answer in [QUESTIONS.md](QUESTIONS.md).)

### 4. From model to production (2 min)
- **Packages:** model, sealed feature code, model card, hashes. The VPS refuses a package whose code differs from
  what it serves, which is how training/serving skew is blocked *by construction*.
- **Three narrow doors:** a read-only tunnel, an upload-only inbox (rrsync) and a forced-command deploy. Nothing
  has a general shell on production, including my AI pair.
- **Governance:**
  - the database enforces the lifecycle (triggers), so a bug or a manual query can't approve a model;
  - every decision is in a hash-chained audit log;
  - `intel reproduce` rebuilds the production model bit-exactly.
- **Delivery:** CI builds the image once, scans it (trivy, pip-audit) and deploys that digest. A smoke test runs in
  the new container, and a failure rolls back automatically (~40 s).

### 5. Production behaviour (2 min)
- **Shadow ledger:** each post is scored before its outcome exists, by both the new model and the old system. That's
  the evidence for everything after this point.
- **Monitoring:** PSI drift *with a significance test*, because at small n raw PSI cries wolf. It also checks live
  accuracy and calibration. Drift asks the Mac to retrain early, with a cool-down.
- **Guardrail:** a McNemar test on the same posts against the previous system, needing at least 40 outcomes. It
  rolls back to an approved version, audits it, explains it on Telegram, has a 7-day cool-down, and the owner can
  `/keep` a model.
- **SLOs:** 99.5% availability and p95 < 300 ms, from an outside-in probe (traffic is too low for request ratios).
  Multi-window burn-rate alerts, 30 alert-rule unit tests, runbook links in every alert, and a dead-man's switch for
  "the box itself died".
- **Reliability:** a weekly restore drill (RPO 18 h, restore 4 s), postmortems, and the host as an Ansible playbook
  (pull mode, so the doors stay closed).

### 6. Judgement: what I didn't build, and why (1 min)
- **No Kubernetes, feature store, Kubeflow or blue/green.** Each has a measured trigger in `SCALING.md`.
- **Capacity:** about 70 req/s per process, p95 230 ms at 10 clients, about 50,000× above today's load.
- **The first real limit is not "add workers".** Every process runs the background loops, so the first step is
  splitting the stateless API from a singleton worker.
- **Privacy** *(built, merge pending)*: when I measured, the pull copied 41 tables while the models used 5.
  Purpose limitation, a fairness gate with lineage, and audited retention are all expressed as tests.

### 7. What I'd do next / with a team (1 min)
See [TRADEOFFS.md](TRADEOFFS.md#with-a-10-team-or-100-the-data). One line: **split serving from workers, a queue for the
ledger, a feature store only when there's a second consumer, and an evaluation culture before more models.**

---

## Rehearsal checklist
- [ ] 2-minute version under 2:15, out loud, timed.
- [ ] 10-minute version from the one-pager alone, no notes.
- [ ] Draw the loop on a whiteboard in 60 s (Mac → judge → inbox → approve → serve → ledger → monitor → guardrail).
- [ ] For each of the 12 guarantees in the one-pager: name the mechanism and the number without looking.
- [ ] Three stories ready cold: H6 (guardrail), H2 (contracts catching 3.6×), the bot-token incident.
- [ ] Say "I don't know, but here's how I'd find out" once in practice. It's a senior answer.
