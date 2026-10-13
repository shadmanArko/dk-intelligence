"""Performance learning (Phase 5, S4): what each post actually did, joined to what we know about it before it went out.

    warehouse pull (social_post + social_metrics_snapshot)  ──►  one row per post with growth-normalised results
    + the publisher's calendar (post_key → live_url)        ──►  our agent posts joined to their metrics
    ──► plain statistics with bootstrap confidence intervals ──► a playbook per platform the studio reads

Descriptive statistics, no model: the content and reach scorers' test sets stay untouched (rule 9), and a finding
is only offered when its interval excludes "no difference".
"""
