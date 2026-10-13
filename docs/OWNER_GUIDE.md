# Your guide: what you do, and what you never have to

The studio makes the posts. You decide what goes out. Everything you need is on **one page**.

## Open it
```bash
cd "/Users/shadman.arko/Documents/Work_DoNotTouch/Dhaka Kacchi/dk-intelligence"
uv run python -m intel.studio.console
```
Then open **http://127.0.0.1:8771** (bookmark it). The page is called **Today**. At the top is a calm moving picture of the
studio working (ideas drift from Plan to Drafts like light on a river). Below it is a short list of what waits for you. If it
says "all clear", you are done.

## Your daily five minutes
1. **Approve drafts.** Today says "Approve N drafts". At 08:00 Telegram sends you a link: open it on your phone, watch each
   video, press Approve or Reject. Rejecting is fine and teaches nothing bad: the next plan makes new ones.
2. **Footage proposals.** If Today lists "Accept or reject N footage proposals", read the lines (they describe a clip in plain
   words) and press **Accept all**, or **Reject all** if they look wrong. Nothing changes until you press.
3. **Look at the picture.** If a run is working you will see it. Nothing to do; it asks nothing from you.

That is all. You never need the terminal for this.

## Now and then
- **Make a new plan** (button on Today): about 15 minutes, creates drafts for the next days. They wait for your approval.
- **Let the curator learn more footage** (button): it looks at 20 clips and proposes descriptions; you accept them. Do it a few
  times until the "no shot knowledge yet" number is small.
- **Install the daily check** (button) once you have approved a day or two: from then on the studio tops the calendar up by
  itself (drafts only). Remove it any time.
- **Sundays:** a button writes "what worked" in plain words from your posts' numbers.

## A reminder, so you do not have to remember
When the daily check is installed it also sets a **9:00 reminder**: a notification that says what waits for you ("Approve 12
drafts; accept 3 footage proposals"). It only appears when something waits.

## The decisions that are yours
They are listed on Today until you settle them (the list is `config/studio/owner-todo.yaml`; delete a line or set `done: true`
when decided): all reels or a mix, the mutton price and the lamb and taster boxes, a Telegram message from the Mac, TikTok privacy.

## What the studio will never do
Post anything by itself. Approve its own work. Spend more than its token limit (4 million per plan, you get a warning at 80%).
Put a secret on this Mac. Delete your footage.

## When something looks wrong
- **Today says "Cannot reach the publisher"**: the server is unreachable; try again later. The review page on your phone still works.
- **A run says failed**: read `data/studio/last-summary.md` (it says why). Press "Resume" on the runs page, or run again.
- **A post looks wrong**: Reject it on the review page. Tell me what was wrong and I will fix the rule.
- **Everything else**: the runs page (link on Today) shows every run, idea by idea, with the reason for each held post.
