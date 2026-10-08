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
MODEL_LABEL = {
    "content_scorer": "New model (engagement)",
    "reach_scorer": "Reach model",
    "harness_predictor": "Current predictor",
}
OUTCOME_COL = {"reach_scorer": "label_reach"}  # which result answers each model's question


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


def _latest_outcome(p: dict, col: str = "label") -> tuple[str, dict] | None:
    for h in reversed(HORIZONS):
        o = p["outcomes"].get(h)
        if o and o.get(col) is not None:
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
        reach = " · ".join(
            f"{H(MODEL_LABEL.get(n, n))}: <b>{m['accuracy']:.0%}</b> of {m['n']}"
            for n, m in sorted(s.get("reach", {}).items())
        )
        score += f"<tr><td>after {h}</td><td>{cells or 'no posts measured yet'}</td><td>{reach or '–'}</td></tr>"
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
<h2>How often each model was right</h2><table><tr><th></th><th>Its own question</th>
<th>Reach question (more people than our last 20 posts?)</th></tr>{score}</table>
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
    preds = ""
    names = [
        n
        for n in ("content_scorer", "reach_scorer", "harness_predictor")
        if n != "reach_scorer" or n in p["predictions"]
    ]
    for name in names:
        latest = _latest_outcome(p, OUTCOME_COL.get(name, "label"))
        x = p["predictions"].get(name)
        if not x:
            preds += f"<tr><td>{MODEL_LABEL[name]}</td><td colspan=3 class=muted>not scored</td></tr>"
            continue
        if name == "reach_scorer":
            said = "more reach" if x["predicted_label"] else "less reach"
        else:
            said = "above typical" if x["predicted_label"] else "below typical"
        verdict = ""
        if latest:
            right = x["predicted_label"] == latest[1][OUTCOME_COL.get(name, "label")]
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
        rl = o.get("label_reach")
        rlab = "" if rl is None else (" <span class=ok>▲</span>" if rl else " <span class=bad>▼</span>")
        rmed = "" if o.get("reach_median") is None else f" (recent {o['reach_median']:.0f})"
        outs += (
            f"<td>reach {o['reach'] if o['reach'] is not None else '–'}{rmed}{rlab}<br>engagement "
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


def _pct(x) -> str:
    return "–" if x is None else f"{x:.0%}"


def insights_html(bundle: dict | None) -> str:
    if not bundle:
        return (
            "<h1>Insights</h1><p class=muted>No insights approved yet. The competitor review miner runs every Sunday on "
            "the Mac; its first version needs your 200-sentence check (Control Room → Review check), then your "
            "approval in Telegram.</p>"
        )
    ins, chk = bundle["insights"], bundle.get("check") or {}
    rows = "".join(
        f"<tr><td><b>{H(a['label'])}</b></td><td>{_pct(a['share_of_reviews'])}</td>"
        f"<td class={'bad' if a['negative_rate'] >= 0.4 else ''}>{_pct(a['negative_rate'])}</td><td>{a['negative']}</td>"
        f"<td>{'<br>'.join('“' + H(x) + '”' for x in a['complaints'][:2])}</td>"
        f"<td>{H(a.get('our_angle') or '')}</td></tr>"
        for a in ins["overall"]
    )
    bir = (
        "".join(
            f"<tr><td>{H(b['label'])}</td><td>{b['mentions']}</td><td>{_pct(b['negative_rate'])}</td>"
            f"<td>{'<br>'.join('“' + H(x) + '”' for x in b.get('complaints', [])[:2])}</td></tr>"
            for b in ins.get("biryani_mentions", [])
        )
        or "<tr><td colspan=4 class=muted>too few mentions</td></tr>"
    )
    dist = "".join(
        f"<tr><td>{H(d['district'])}</td><td>{d['listings']}</td><td>{d['reviews']}</td>"
        f"<td>{', '.join(H(c['label']) + ' ' + _pct(c['negative_rate']) for c in d['top_complaints'])}</td></tr>"
        for d in ins["districts"]
    )
    comp = "".join(
        f"<tr><td>{H(c['name'] or '?')}</td><td>{H(c['district'] or '–')}</td><td>{c['reviews']}</td><td>{c['rating']}</td>"
        f"<td class=bad>{', '.join(H(w['label']) + ' ' + _pct(w['negative_rate']) for w in c['weak']) or '–'}</td>"
        f"<td class=ok>{', '.join(H(w['label']) for w in c['strong']) or '–'}</td></tr>"
        for c in ins["competitors"][:40]
    )
    return f"""<h1>Insights: what Berlin biryani customers complain about</h1>
<p class=muted>{ins["reviews"]:,} Lieferando reviews of {ins["listings"]} Berlin places that sell biryani,
{ins["period"][0]} to {ins["period"][1]}. Version <code>{H(bundle["version"])}</code>. Reading checked by you:
aspect precision {chk.get("aspect_precision")}, recall {chk.get("aspect_recall")}, tone accuracy
{chk.get("polarity_accuracy")} ({chk.get("checked")} sentences). "Our angle" lines are suggestions: use one only if it is
true for us.</p>
<h2>All topics, by number of complaints</h2>
<table><tr><th>Topic</th><th>In reviews</th><th>Negative when mentioned</th><th>Complaints</th><th>Example</th>
<th>Our angle (check before use)</th></tr>{rows}</table>
<h2>When customers mention biryani by name</h2>
<table><tr><th>Topic</th><th>Mentions</th><th>Negative</th><th>Example</th></tr>{bir}</table>
<h2>By district (where the restaurants are)</h2>
<table><tr><th>District</th><th>Places</th><th>Reviews</th><th>Top complaints</th></tr>{dist}</table>
<h2>Competitors: weaker / stronger than the Berlin average</h2>
<table><tr><th>Place</th><th>District</th><th>Reviews</th><th>★</th><th>Weak at</th><th>Strong at</th></tr>{comp}</table>"""


BAND_COLOR = {"≤ 5 km · €5": "var(--ok)", "≤ 10 km · €7": "var(--acc)", "≤ 15 km · €9": "#b8860b"}


def _bubble_map(ins: dict, width: int = 720, height: int = 600) -> str:
    """Plain SVG bubble map (no map tiles, no scripts): one circle per planning area at its centre, size = score,
    colour = delivery band, dark ring = gap (big audience, no biryani seller), star = pickup point."""
    lon0, lon1, lat0, lat1 = 13.08, 13.77, 52.33, 52.68

    def xy(lat, lon):
        return (lon - lon0) / (lon1 - lon0) * width, (lat1 - lat) / (lat1 - lat0) * height

    dots = []
    for a in reversed(ins["areas"]):  # best last = drawn on top
        if a["lat"] is None:
            continue
        x, y = xy(a["lat"], a["lon"])
        r = 2 + 9 * (a["score"] or 0)
        col = BAND_COLOR.get(a["reach_band"], "var(--muted)")
        ring = " stroke='var(--fg)' stroke-width='1.6'" if a["gap"] else ""
        dots.append(
            f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{r:.1f}' fill='{col}' fill-opacity='0.55'{ring}>"
            f"<title>{H(str(a['name']))} ({H(str(a['district']))}): score {a['score']}, {a['distance_km']} km"
            f"{', gap' if a['gap'] else ''}</title></circle>"
        )
    ox, oy = xy(ins["origin"]["lat"], ins["origin"]["lon"])
    legend = "".join(f"<tspan fill='{c}'>● </tspan><tspan>{H(b)}   </tspan>" for b, c in BAND_COLOR.items())
    return (
        f"<svg viewBox='0 0 {width} {height + 30}' role=img aria-label='Berlin areas by score' "
        f"style='width:100%;max-width:{width}px;background:var(--card);border:1px solid var(--line);border-radius:10px'>"
        + "".join(dots)
        + f"<text x='{ox:.0f}' y='{oy + 5:.0f}' text-anchor=middle font-size='18' fill='var(--bad)'>★</text>"
        f"<text x='12' y='{height + 20}' font-size='12' fill='var(--muted)'>{legend}<tspan>○ ring = gap · ★ pickup "
        f"Leopoldplatz · bubble size = score</tspan></text></svg>"
    )


def community_html(bundle: dict | None) -> str:
    if not bundle:
        return (
            "<h2>Community map</h2><p class=muted>Not approved yet: it is built every Sunday and offered for approval "
            "in Telegram.</p>"
        )
    ins = bundle["insights"]
    by_id = {a["area_id"]: a for a in ins["areas"]}

    def row(a):
        return (
            f"<tr><td><b>{H(str(a['name']))}</b></td><td>{H(str(a['district']))}</td><td>{a['distance_km']} km</td>"
            f"<td>{a['score']}</td><td>{a['south_asian']}</td><td>{a['meat_rice_cultures']}</td>"
            f"<td>{a['internationals']}</td><td>{a['young_small_households']}</td><td>{a['biryani_listings']}</td>"
            f"<td>{'<b class=ok>gap</b>' if a['gap'] else ''}</td><td>{a['orders'] or ''}</td></tr>"
        )

    head = (
        "<tr><th>Area</th><th>District</th><th>From pickup</th><th>Score</th><th>South Asian origin</th>"
        "<th>Meat & rice cultures</th><th>Internationals</th><th>Young, small households</th><th>Biryani sellers</th>"
        "<th></th><th>Your orders</th></tr>"
    )
    top = "".join(row(a) for a in ins["areas"][:25])
    aud = "".join(row(by_id[i]) for i in ins.get("top_audience", [])[:15] if i in by_id)
    gaps = [a for a in ins["areas"] if a["gap"]][:15]
    dist = "".join(
        f"<tr><td>{H(d['district'])}</td><td>{d['score']}</td><td>{d['distance_km']} km</td><td>{d['south_asian']:,}</td>"
        f"<td>{d['meat_rice_cultures']:,}</td><td>{d['biryani_listings']}</td><td>{d['gaps']}</td><td>{d['orders'] or ''}</td></tr>"
        for d in ins["districts"]
    )
    w = ", ".join(f"{k.replace('_', ' ')} {v:.0%}" for k, v in ins["weights"].items())
    return f"""<h2>Community map: where to deliver, advertise and pop up</h2>
<p class=muted>{ins["n_areas"]} Berlin planning areas (LOR), version <code>{H(bundle["version"])}</code>. Score = audience
({H(w)}; percentile ranks of public statistics) × reach factor of the delivery band from Leopoldplatz. South Asian
origin is published per area only for India; Bangladeshi and Pakistani residents are counted citywide only. The census
religion variable is deliberately not used. Use this to choose AREAS (delivery zones, area-targeted ads, flyers,
pop-ups), never to target or exclude people. Your orders so far located: {ins["orders_located"]}.</p>
{_bubble_map(ins)}
<h3>Best areas overall (delivery)</h3><table>{head}{top}</table>
<h3>Gaps: big audience, no biryani seller in the area</h3><table>{head}{"".join(row(a) for a in gaps)}</table>
<h3>Biggest audience regardless of distance (pop-ups, events, ads)</h3><table>{head}{aud}</table>
<h3>Districts</h3><table><tr><th>District</th><th>Avg score</th><th>Median distance</th><th>South Asian origin</th>
<th>Meat & rice cultures</th><th>Biryani sellers</th><th>Gaps</th><th>Your orders</th></tr>{dist}</table>"""


def planner_html(bundle: dict | None) -> str:
    if not bundle:
        return (
            "<h2>Occasion plan</h2><p class=muted>Not approved yet: it is built every Sunday and offered for approval "
            "in Telegram (after your first approval it refreshes automatically).</p>"
        )
    ins = bundle["insights"]

    def lift(v):
        return "–" if v is None else (f"<span class=ok>+{v:.2f}</span>" if v > 0 else f"<span class=bad>{v:.2f}</span>")

    rows = (
        "".join(
            f"<tr><td><b>{H(p['label'])}</b>{'<br><span class=muted>date estimated</span>' if p['estimated_date'] else ''}</td>"
            f"<td>{H(p['community_id'])}</td><td>{p['people']:,}</td><td>{p['first_day']}"
            f"{'–' + p['last_day'] if p['last_day'] != p['first_day'] else ''}</td><td><b>{p['start_posting']}</b></td>"
            f"<td>{H(p['food_role'])}</td><td>{lift(p['market_lift'])}</td>"
            f"<td>{H(p['greeting'] or '')}{' <span class=muted>(native check)</span>' if p['greeting'] and p['greeting_needs_review'] else ''}"
            f"{'<br><span class=muted>' + H(p['greeting_translit']) + '</span>' if p.get('greeting_translit') else ''}</td>"
            f"<td>{H(', '.join(p['bridge_dishes']))}</td></tr>"
            for p in ins["picks"]
        )
        or "<tr><td colspan=9>nothing above the thresholds</td></tr>"
    )
    notes = (
        "".join(
            f"<li>{H(n['day'])}: {H(n['label'])} ({H(n['community'])}): {H(n['advice'])}</li>"
            for n in ins["respectful_notes"]
        )
        or "<li>none</li>"
    )
    events = (
        "".join(
            f"<li>{H(e['event'])} ({H(', '.join(e['days'][:3]))}{'…' if len(e['days']) > 3 else ''})</li>"
            for e in ins["berlin_events"]
        )
        or "<li>none listed</li>"
    )
    t = ins.get("timing") or {}

    def tim(col):
        rows_ = sorted(t.get(col, []), key=lambda r: (r["platform"], -r["above_recent"]))
        return "".join(
            f"<tr><td>{H(r['platform'])}</td><td>{H(r[col])}</td><td>{r['above_recent']:.0%}</td><td>{r['posts']}</td></tr>"
            for r in rows_
        )

    return f"""<h2>Occasion plan: {ins["from"]} to {ins["to"]}</h2>
<p class=muted>Version <code>{H(bundle["version"])}</code>. Ranked by food role × √(Berliners who celebrate it); religious
occasions are shown with their neutral names and greetings (content rules), blocked words are checked automatically.
"Start posting" is the lead time before the day. Market lift: how posts about this occasion did on TikTok vs usual.</p>
<table><tr><th>Occasion</th><th>Audience</th><th>Berliners</th><th>Day(s)</th><th>Start posting</th><th>Food role</th>
<th>Market lift</th><th>Greeting</th><th>Their rice dishes (bridge to kacchi)</th></tr>{rows}</table>
<h3>Respectful notes (no sales posts)</h3><ul>{notes}</ul>
<h3>Berlin events (pop-up ideas)</h3><ul>{events}</ul>
<h3>When our posts reached more people than our recent posts ({t.get("posts", 0)} posts)</h3>
<p class=muted>Share of posts above the median reach of the previous 20 posts. Small samples: treat differences under
~15 points as noise.</p>
<div class=actions><table><tr><th>Platform</th><th>Weekday</th><th>Above recent</th><th>Posts</th></tr>{tim("weekday")}</table>
<table><tr><th>Platform</th><th>Time (Berlin)</th><th>Above recent</th><th>Posts</th></tr>{tim("band")}</table></div>"""


def funnel_html(bundle: dict | None) -> str:
    if not bundle:
        return (
            "<h2>Funnel</h2><p class=muted>Not approved yet: it is built every Sunday and offered for approval in "
            "Telegram (after your first approval it refreshes automatically).</p>"
        )
    ins = bundle["insights"]
    t = ins["totals"]
    steps = [
        ("visit", "Visits"),
        ("menu", "Menu / order page"),
        ("cart", "Cart started"),
        ("checkout", "Checkout"),
        ("submitted", "Order submitted"),
        ("ordered", "Real order (not cancelled)"),
    ]
    top = max(t["visit"], 1)
    bars = "".join(
        f"<tr><td>{label}</td><td><div style='background:var(--acc);height:14px;border-radius:4px;"
        f"width:{max(2, round(300 * t[k] / top))}px'></div></td><td><b>{t[k]}</b></td>"
        f"<td class=muted>{(t[k] / top):.0%}</td></tr>"
        for k, label in steps
    )
    ch = "".join(
        f"<tr><td>{H(c['channel'])}</td>" + "".join(f"<td>{c[k]}</td>" for k, _ in steps) + "</tr>"
        for c in ins["channels"]
    )
    fr = ins["checkout_friction"]
    fric = "".join(f"<li>{H(k)}: {v}</li>" for k, v in fr["with"].items() if v) or "<li>no errors recorded</li>"
    posts = (
        "".join(
            f"<tr><td>{H(p['platform'])}</td><td>{H(p['posted_at'][:16].replace('T', ' '))}</td><td>{H(p['format'] or '')}</td>"
            f"<td>{H(p['caption'])}</td><td>{p['sessions']}</td><td>{p['carts']}</td><td>{p['orders']}</td></tr>"
            for p in ins["posts"]
        )
        or "<tr><td colspan=7 class=muted>no platform visits after posts yet</td></tr>"
    )
    advice = "".join(f"<li>{H(a)}</li>" for a in ins["advice"])
    return f"""<h2>Funnel: from visit to order ({H(ins["period"][0])} to {H(ins["period"][1])})</h2>
<p class=muted>Version <code>{H(bundle["version"])}</code>. From the website analytics (test hosts excluded); orders are
matched to the visit that submitted them (same total within 3 minutes). Only counts are shown.
Unknown source: <b>{ins["unknown_share"]:.0%}</b> of visits · tracked links so far: <b>{ins["tracked_links"]}</b>.</p>
<table>{bars}</table>
<h3>What to do</h3><ol>{advice}</ol>
<h3>Stuck at checkout: {fr["stuck_sessions"]} of {fr["reached_checkout"]} visits (sessions with…)</h3><ul>{fric}</ul>
<h3>By source</h3><table><tr><th>Source</th>{"".join(f"<th>{label}</th>" for _, label in steps)}</tr>{ch}</table>
<h3>Visits from a platform within 48 h after each post (estimate)</h3>
<p class=muted>Without tracked links this is only timing: a visit from Facebook after a Facebook post may have come from
an older post or the page itself.</p>
<table><tr><th>Platform</th><th>Posted (UTC)</th><th>Format</th><th>Caption</th><th>Visits</th><th>Carts</th><th>Orders</th></tr>{posts}</table>"""
