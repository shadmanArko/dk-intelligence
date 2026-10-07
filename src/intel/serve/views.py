"""Human pages for the ledger: what each model predicted for every real post, why, and what really happened.

Captions are not stored in intel-db (only a hash); the page reads caption + permalink live from the warehouse
(read-only role) when it is reachable, and still works without them.
"""

from __future__ import annotations

import html
import logging
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from intel.serve.db import connect

log = logging.getLogger("intel.views")
H = html.escape
BERLIN = ZoneInfo("Europe/Berlin")
HORIZONS = ("24h", "72h", "7d")
MODEL_LABEL = {"content_scorer": "New model", "harness_predictor": "Current predictor"}


def reason_text(feature: str) -> str:
    """'nominal__day_of_week_Saturday' -> 'Day: Saturday' (the champion's hour bands are in UTC)."""
    f = feature.split("__", 1)[-1]
    for prefix, label in (
        ("day_of_week_", "Day"),
        ("hour_bucket_", "Time band (UTC)"),
        ("platform_", "Platform"),
        ("content_type_filled_", "Format"),
        ("hashtag_band_", "Hashtags"),
    ):
        if f.startswith(prefix):
            return f"{label}: {f.removeprefix(prefix)}"
    return {
        "is_video_True": "Video",
        "is_video_False": "Not a video",
        "caption_length": "Caption length",
        "hashtag_count": "Number of hashtags",
        "first_line_length": "First line length",
        "script_bengali": "Bengali script",
    }.get(f, f.replace("_", " "))


def reasons_html(reasons: list[dict] | None) -> str:
    if not reasons:
        return ""
    return "".join(
        f"<span class='{'up' if r['contribution'] > 0 else 'down'}'>{'▲' if r['contribution'] > 0 else '▼'} "
        f"{H(reason_text(r['feature']))}</span> "
        for r in reasons
    )


def ledger_rows(intel_dsn: str, warehouse_dsn: str | None, limit: int = 100) -> list[dict]:
    with connect(intel_dsn) as c:
        posts = c.execute("select * from ledger_post order by posted_at desc limit %s", (limit,)).fetchall()
        ids = [p["post_id"] for p in posts]
        preds = c.execute("select * from prediction where source='shadow' and post_id = any(%s)", (ids,)).fetchall()
        outs = c.execute("select * from post_outcome where post_id = any(%s)", (ids,)).fetchall()
    extra = {}
    if warehouse_dsn and ids:
        try:
            with psycopg.connect(warehouse_dsn, row_factory=dict_row, connect_timeout=5) as w:
                w.read_only = True
                for r in w.execute("select id, caption, permalink from social_post where id = any(%s)", (ids,)):
                    extra[r["id"]] = r
        except Exception:
            log.warning("warehouse not reachable for captions")
    for p in posts:
        p["predictions"] = {x["model_name"]: x for x in preds if x["post_id"] == p["post_id"]}
        p["outcomes"] = {o["horizon"]: o for o in outs if o["post_id"] == p["post_id"]}
        p["caption"] = (extra.get(p["post_id"]) or {}).get("caption")
        p["permalink"] = (extra.get(p["post_id"]) or {}).get("permalink")
    return posts


def _latest_outcome(p: dict) -> tuple[str, dict] | None:
    for h in reversed(HORIZONS):
        o = p["outcomes"].get(h)
        if o and o["label"] is not None:
            return h, o
    return None


def posts_html(rows: list[dict], summaries: dict[str, dict]) -> str:
    score = ""
    for h in HORIZONS:
        s = summaries[h]
        cells = " · ".join(
            f"{H(MODEL_LABEL.get(n, n))}: <b>{m['accuracy']:.0%}</b> right of {m['n']}"
            for n, m in sorted(s["models"].items())
        )
        score += f"<tr><td>after {h}</td><td>{cells or 'no posts measured yet'}</td></tr>"
    if not rows:
        body = (
            "<p class=muted>No posts in the ledger yet. Every Facebook / Instagram post published from 7 October "
            "2026 appears here within about an hour of the daily warehouse import (04:30–05:00).</p>"
        )
    else:
        body = "".join(_post_card(p) for p in rows)
    return f"""<h1>Posts: predicted vs real</h1>
<p class=muted>Each post is scored once, using only what was known before posting (platform, format, time, caption).
"Above typical" means more likes + comments + shares than the median of your earlier posts on that platform.</p>
<h2>How often each model was right</h2><table>{score}</table>
<h2>Posts</h2>{body}"""


def _post_card(p: dict) -> str:
    when = p["posted_at"].astimezone(BERLIN)
    cap = p["caption"]
    cap_html = (
        H(cap[:220] + ("…" if len(cap) > 220 else ""))
        if cap
        else f"<span class=muted>({p['caption_length']} characters)</span>"
    )
    link = f" · <a href='{H(p['permalink'])}' target=_blank rel=noopener>open post</a>" if p["permalink"] else ""
    latest = _latest_outcome(p)
    preds = ""
    for name in ("content_scorer", "harness_predictor"):
        x = p["predictions"].get(name)
        if not x:
            preds += f"<tr><td>{MODEL_LABEL[name]}</td><td colspan=3 class=muted>not scored</td></tr>"
            continue
        said = "above typical" if x["predicted_label"] else "below typical"
        verdict = ""
        if latest:
            right = x["predicted_label"] == latest[1]["label"]
            verdict = f"<span class={'ok' if right else 'bad'}>{'✓ right' if right else '✗ wrong'}</span> <span class=muted>({latest[0]})</span>"
        preds += (
            f"<tr><td>{MODEL_LABEL[name]}</td><td>{x['probability']:.0%} → <b>{said}</b></td>"
            f"<td>{reasons_html(x['top_reasons'])}</td><td>{verdict or '<span class=muted>waiting</span>'}</td></tr>"
        )
    outs = ""
    for h in HORIZONS:
        o = p["outcomes"].get(h)
        if not o:
            outs += "<td class=muted>–</td>"
            continue
        lab = (
            ""
            if o["label"] is None
            else (" <span class=ok>▲ above</span>" if o["label"] else " <span class=bad>▼ below</span>")
        )
        med = "" if o["platform_median"] is None else f" (typical {o['platform_median']:.0f})"
        outs += (
            f"<td>reach {o['reach'] if o['reach'] is not None else '–'} · engagement "
            f"{o['engagement'] if o['engagement'] is not None else '–'}{med}{lab}</td>"
        )
    return f"""<div class=card><p><b>{when:%a %d %b %Y, %H:%M}</b> · {H(p["platform"])} · {H(p["content_type"] or "unknown format")}{link}</p>
<p>{cap_html}</p>
<table><tr><th>Model</th><th>Prediction</th><th>Why</th><th>Result</th></tr>{preds}</table>
<table><tr><th>After 24h</th><th>After 72h</th><th>After 7 days</th></tr><tr>{outs}</tr></table></div>"""


def try_form_html(result: dict | None = None, form: dict | None = None, error: str | None = None) -> str:
    f = form or {"platform": "instagram", "content_type": "reel", "caption": "", "when": ""}

    def opt(name: str, values: list[str]) -> str:
        return "".join(f"<option{' selected' if f.get(name) == v else ''}>{H(v)}</option>" for v in values)

    res = ""
    if error:
        res = f"<div class=card><p class=bad>{H(error)}</p></div>"
    elif result:
        said = "above typical" if result["label"] else "below typical"
        res = (
            f"<div class=card><h2>{result['probability']:.0%} chance to beat your typical "
            f"{H(f['platform'])} post → <b>{said}</b></h2><p>Why: {reasons_html(result['top_reasons'])}</p>"
            f"<p class=muted>Model {H(result['model_version'])}, threshold {result['threshold']:.0%}. This is an "
            f"estimate from ~370 past posts: use it to compare drafts, not as a promise.</p></div>"
        )
    return f"""<h1>Try a draft</h1>
<p class=muted>Score a post before publishing. Nothing is posted; the request is logged without the caption text.</p>
<form method=post action=/try class=card><input type=hidden name=csrf value='{{csrf}}'>
<p><label>Platform <select name=platform>{opt("platform", ["instagram", "facebook"])}</select></label>
<label>Format <select name=content_type>{opt("content_type", ["reel", "video", "image", "carousel"])}</select></label>
<label>Planned time (Berlin) <input type=datetime-local name=when value='{H(f.get("when", ""))}' required></label></p>
<p><textarea name=caption rows=6 maxlength=5000 placeholder="Caption, hashtags included">{H(f.get("caption", ""))}</textarea></p>
<button>Score it</button></form>{res}"""


GITHUB_ACTIONS = "https://github.com/shadmanArko/dk-intelligence/actions"
STEP_ICON = {
    "done": ("✓", "ok"),
    "running": ("⏳", ""),
    "failed": ("✗", "bad"),
    "skipped": ("–", "muted"),
    "pending": ("·", "muted"),
}


def _t(v) -> str:
    if not v:
        return ""
    t = v if hasattr(v, "astimezone") else __import__("datetime").datetime.fromisoformat(v)
    return f"{t.astimezone(BERLIN):%a %d %b %H:%M}"


def training_html(o: dict, deployed: dict) -> str:
    p = o["progress"]
    if p:
        head = (
            "⏳ running now"
            if p["state"] == "running" and not o["stalled"]
            else f"finished: <b>{H(p.get('result') or '?')}</b>"
            if p["state"] == "finished"
            else "⚠️ stalled"
        )
        rows = ""
        from intel.pipeline import STEPS

        for st in (p["steps"][k] for k in STEPS if k in p["steps"]):  # jsonb does not keep key order
            icon, cls = STEP_ICON.get(st["status"], ("?", ""))
            rows += (
                f"<tr><td class='{cls}'>{icon}</td><td>{H(st['label'])}</td><td>{H(st['status'])}</td>"
                f"<td>{_t(st.get('started_at'))}</td><td>{_t(st.get('finished_at'))}</td>"
                f"<td>{H(st.get('note') or '')}</td></tr>"
            )
        stalled = ""
        if o["stalled"]:
            stalled = (
                f"<p class=bad>No update for {int(o['stalled'].total_seconds() // 3600)} hours. The Mac is probably "
                "asleep or was switched off during the run. When it is back on, the run starts again by itself "
                "(it is still due), or start it by hand.</p>"
            )
        current = f"""<div class=card><h2>Run {H(p["run_id"])}: {head}</h2>
<p class=muted>Started {_t(p["started_at"])} · last update {_t(p["updated_at"])} (Berlin time)</p>{stalled}
<table><tr><th></th><th>Step</th><th>Status</th><th>Started</th><th>Finished</th><th>Note</th></tr>{rows}</table></div>"""
    else:
        current = (
            "<p class=muted>No training progress received yet. It appears here as soon as the Mac starts a run.</p>"
        )
    hist = (
        "".join(
            f"<tr><td>{H(r['run_id'])}</td><td><span class='tag {'approved' if r['status'] in ('pass', 'baseline') else 'rejected' if r['status'] == 'error' else ''}'>{H(r['status'])}</span></td>"
            f"<td>{H(r['failed_step'] or '')}</td>"
            f"<td>{(r['report'].get('challenger') or {}).get('balanced_accuracy', '–')} vs {(r['report'].get('champion') or {}).get('balanced_accuracy', '–')}</td>"
            f"<td>{H(', '.join(r['report'].get('failed_gates') or []))}</td><td>{_t(r['received_at'])}</td></tr>"
            for r in o["runs"]
        )
        or "<tr><td colspan=6 class=muted>No runs received yet.</td></tr>"
    )
    return f"""<h1>Training</h1>
<div class=card><p><b>This week:</b> {H(o["this_week"])}<br>
<b>Last successful run:</b> {_t(o["last_ok"]) or "none yet"} · <b>Next regular run:</b> {o["next_anchor"].astimezone(BERLIN):%a %d %b} 12:00</p>
<p class=muted>The Mac checks every hour and at start-up; if this week's run has not happened (for example the Mac was off
on Sunday), it starts within an hour of the Mac being on. A failed run is retried after {o["retry_after_h"]} hours.
To run it now: double-click <b>Run Sunday training</b> in the dk-intelligence folder.</p></div>
<h2>Current / last run</h2>{current}
<h2>Recent runs</h2><table><tr><th>Run</th><th>Result</th><th>Failed at</th><th>New vs current</th><th>Failed checks</th>
<th>Received</th></tr>{hist}</table>
<h2>VPS code</h2><p>Deployed version <code>{H(deployed["sha"])}</code>, running since {_t(deployed["started_at"])}.
Deploys happen automatically after every push to main: <a href="{GITHUB_ACTIONS}" target=_blank rel=noopener>GitHub Actions</a>.</p>"""
