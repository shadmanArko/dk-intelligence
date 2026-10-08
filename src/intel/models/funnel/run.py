"""Funnel and attribution plumbing: PostHog sessions -> checkout steps -> orders, by entry channel and by post.

Privacy: only aggregates leave this module (counts per channel / week / post). Session ids, visitor ids, emails and
postcodes are used for joining only and an automatic gate scans the output for them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
from pathlib import Path

import joblib
import pandas as pd
import yaml

from intel.judge import gate

SPEC_FILE = Path(__file__).with_name("spec.yaml")
SPEC = yaml.safe_load(SPEC_FILE.read_text())
STEPS = ["visit", "menu", "cart", "checkout", "submitted", "ordered"]
EVENT_STEP = {
    "order_cart_started": "cart",
    "order_checkout_clicked": "checkout",
    "order_fulfillment_selected": "checkout",
    "order_delivery_fee_quoted": "checkout",
    "order_delivery_date_selected": "checkout",
    "order_submitted": "submitted",
}
CHANNELS = [
    ("facebook", r"facebook|fb\.|l\.facebook"),
    ("instagram", r"instagram"),
    ("threads", r"threads"),
    ("whatsapp", r"wl\.co|wa\.me|whatsapp"),
    ("youtube", r"youtube|youtu\.be"),
    ("tiktok", r"tiktok"),
    ("google", r"google\."),
    ("other search", r"bing|duckduckgo|yandex|ecosia|yahoo"),
    ("lieferando/wolt", r"lieferando|wolt"),
]
FRICTION = {
    "auth_login_failed": "login failed",
    "auth_registration_failed": "registration failed",
    "order_delivery_not_available": "delivery not available",
    "order_submission_failed": "order submission failed",
    "$rageclick": "rage clicks (repeated clicking)",
}
PRIVATE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-|[0-9a-f]{24,}|@[a-z0-9-]+\.[a-z]{2,}|\b1[0-4]\d{3}\b", re.I)


def channel(utm_source: str | None, referring_domain: str | None) -> str:
    u = (utm_source or "").lower().strip()
    if u:
        for name, pat in CHANNELS:
            if re.search(pat, u):
                return name
        return u[:24]
    d = (referring_domain or "").lower().strip()
    if not d or d == "$direct":
        return "direct / unknown"
    for name, pat in CHANNELS:
        if re.search(pat, d):
            return name
    return "other site"


def sessions(pull_dir: Path) -> pd.DataFrame:
    ph = pd.read_parquet(pull_dir / "raw_posthog_events.parquet", columns=["occurred_at", "payload"])
    rows = []
    for ts, raw in zip(ph["occurred_at"], ph["payload"], strict=True):
        x = json.loads(raw)
        pr = x.get("properties") or {}
        sid = pr.get("$session_id")
        host = str(pr.get("$host") or "")
        if not sid or any(h in host for h in SPEC["test_hosts"]) or x["event"].startswith("debug"):
            continue
        rows.append(
            {
                "sid": sid,
                "ts": pd.Timestamp(ts),
                "event": x["event"],
                "path": pr.get("$pathname") or "",
                "ref": pr.get("$session_entry_referring_domain"),
                "utm": pr.get("$session_entry_utm_source"),
                "utm_content": pr.get("$session_entry_utm_content"),
                "total": pr.get("totalCents"),
            }
        )
    ev = pd.DataFrame(rows)
    if ev.empty:
        return pd.DataFrame(columns=["sid", "start", "channel", *STEPS])
    out = []
    for sid, g in ev.sort_values("ts").groupby("sid"):
        ref = next((r for r in g["ref"] if isinstance(r, str) and r), None)
        utm = next((u for u in g["utm"] if isinstance(u, str) and u), None)
        steps = {s: False for s in STEPS}
        steps["visit"] = True
        steps["menu"] = bool(g["path"].str.contains("menu|order|bestell", case=False).any()) or bool(
            g["event"].isin(EVENT_STEP).any()
        )
        for e in g["event"]:
            if e in EVENT_STEP:
                steps[EVENT_STEP[e]] = True
        if steps["submitted"]:
            steps["checkout"] = steps["cart"] = steps["menu"] = True
        friction = {f"f_{k}": int((g["event"] == k).sum()) for k in FRICTION}
        sub = g[g["event"] == "order_submitted"]
        out.append(
            {
                "sid": sid,
                "start": g["ts"].iloc[0],
                "channel": channel(utm, ref),
                "utm_content": next((u for u in g["utm_content"] if isinstance(u, str) and u), None),
                "submitted_at": sub["ts"].iloc[0] if len(sub) else None,
                "submitted_total": sub["total"].iloc[0] if len(sub) else None,
                **steps,
                **friction,
            }
        )
    return pd.DataFrame(out)


def match_orders(sess: pd.DataFrame, pull_dir: Path) -> pd.DataFrame:
    """order_submitted <-> order: same total (subtotal + delivery fee) within match_window_s."""
    ro = pd.read_parquet(pull_dir / "raw_orders_direct.parquet", columns=["payload"])
    orders = [json.loads(p) for p in ro["payload"]]
    sess["ordered"], sess["order_status"] = False, None
    used = set()
    for i, s in sess[sess["submitted_at"].notna()].iterrows():
        for o in orders:
            if o["id"] in used:
                continue
            total = (o.get("subtotal_cents") or 0) + (o.get("delivery_fee_cents") or 0)
            dtm = abs((pd.Timestamp(o["created_at"]) - s["submitted_at"]).total_seconds())
            if total == s["submitted_total"] and dtm <= SPEC["match_window_s"]:
                sess.loc[i, ["ordered", "order_status"]] = [o.get("status") != "cancelled", o.get("status")]
                used.add(o["id"])
                break
    return sess


def by_channel(sess: pd.DataFrame) -> list[dict]:
    rows = []
    for ch, g in sess.groupby("channel"):
        rows.append({"channel": ch, **{s: int(g[s].sum()) for s in STEPS}})
    return sorted(rows, key=lambda r: -r["visit"])


def per_post(sess: pd.DataFrame, pull_dir: Path) -> list[dict]:
    """Soft attribution: sessions from a platform within post_window_h after each post (cut at the next post)."""
    posts = pd.read_parquet(
        pull_dir / "social_post.parquet", columns=["id", "platform", "posted_at", "content_type", "caption"]
    )
    posts = posts[posts["platform"].isin(["facebook", "instagram", "threads"])].sort_values("posted_at")
    out = []
    for plat, g in posts.groupby("platform"):
        times = list(g["posted_at"]) + [pd.Timestamp.max.tz_localize("UTC")]
        ps = sess[sess["channel"] == plat]
        for k, p in enumerate(g.itertuples()):
            end = min(p.posted_at + pd.Timedelta(hours=SPEC["post_window_h"]), times[k + 1])
            w = ps[(ps["start"] >= p.posted_at) & (ps["start"] < end)]
            if len(w):
                out.append(
                    {
                        "platform": plat,
                        "posted_at": p.posted_at.isoformat(),
                        "format": p.content_type if isinstance(p.content_type, str) else None,
                        "caption": (p.caption if isinstance(p.caption, str) else "")[:80],
                        "sessions": len(w),
                        "carts": int(w["cart"].sum()),
                        "orders": int(w["ordered"].sum()),
                    }
                )
    return sorted(out, key=lambda r: (-r["orders"], -r["sessions"]))[:30]


def all_strings(obj) -> list[str]:
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in all_strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in all_strings(v)]
    return [str(obj)] if obj is not None else []


def summarise(sess: pd.DataFrame, pull_dir: Path) -> dict:
    tl = pd.read_parquet(pull_dir / "tracked_link.parquet")
    weekly = (
        sess.assign(week=sess["start"].dt.tz_convert("Europe/Berlin").dt.strftime("%G-W%V"))
        .groupby(["week", "channel"])
        .size()
        .reset_index(name="sessions")
    )
    known = sess[sess["channel"] != "direct / unknown"]
    totals = {s: int(sess[s].sum()) for s in STEPS}
    ordered = sess[sess["ordered"]]
    return {
        "sessions": int(len(sess)),
        "period": [sess["start"].min().date().isoformat(), sess["start"].max().date().isoformat()]
        if len(sess)
        else None,
        "unknown_share": round(1 - len(known) / len(sess), 3) if len(sess) else None,
        "totals": totals,
        "channels": by_channel(sess),
        "orders_by_channel": ordered.groupby("channel").size().to_dict(),
        "weekly": weekly.to_dict("records"),
        "posts": per_post(sess, pull_dir),
        "tracked_links": int(len(tl)),
        "checkout_friction": _friction(sess),
        "utm_content_sessions": sess["utm_content"].dropna().value_counts().head(10).to_dict(),
        "advice": _advice(sess, len(tl)),
    }


def _friction(sess: pd.DataFrame) -> dict:
    """What happened in sessions that reached checkout but did not submit (counts of sessions, not events)."""
    stuck = sess[sess["checkout"] & ~sess["submitted"]]
    return {
        "reached_checkout": int(sess["checkout"].sum()),
        "submitted": int(sess["submitted"].sum()),
        "stuck_sessions": int(len(stuck)),
        "with": {label: int((stuck[f"f_{k}"] > 0).sum()) if f"f_{k}" in stuck else 0 for k, label in FRICTION.items()},
    }


def _advice(sess: pd.DataFrame, n_links: int) -> list[str]:
    a = []
    unknown = (sess["channel"] == "direct / unknown").mean() if len(sess) else 0
    if unknown > 0.5:
        a.append(
            f"{unknown:.0%} of visits have no known source: links opened inside WhatsApp, Instagram or Facebook apps "
            "often lose it. Put a tagged link from the link builder in every post, story, bio and WhatsApp message."
        )
    if n_links == 0:
        a.append(
            "No tracked links yet: create one per post in the admin Link builder; each order can then be "
            "credited to the exact post."
        )
    if (sess["channel"] == "whatsapp").any():
        a.append("WhatsApp brings visits: use a link with utm_source=whatsapp in status updates and broadcasts.")
    if sess["checkout"].sum() and sess["submitted"].sum() / sess["checkout"].sum() < 0.4:
        a.insert(
            0,
            f"Only {int(sess['submitted'].sum())} of {int(sess['checkout'].sum())} visits that reached checkout "
            "submitted an order: test the checkout on a phone (login / OTP step, delivery-area message) and "
            "consider ordering without an account.",
        )
    a.append(
        "Where links are not allowed (TikTok captions, Reels): use a short code (e.g. 'KACCHI10') or 'comment "
        "KACCHI for the link', so those orders can be counted too."
    )
    return a


def run(pull_dir: Path, runs_dir: Path, log=print) -> Path:
    sess = match_orders(sessions(pull_dir), pull_dir)
    ins = summarise(sess, pull_dir)
    log(
        f"funnel: {ins['sessions']} sessions, {ins['totals']['ordered']} orders matched, "
        f"{ins['unknown_share']:.0%} unknown source"
    )
    leaks = sorted(
        {
            m.group(0)[:12]
            for s in all_strings({k: v for k, v in ins.items() if k != "advice"})
            for m in [PRIVATE.search(s)]
            if m
        }
    )
    now = dt.datetime.now(dt.UTC)
    version = (
        f"fn-{now:%Y%m%dT%H%M%S}-{hashlib.sha256((pull_dir.name + SPEC_FILE.read_text()).encode()).hexdigest()[:6]}"
    )
    bundle = {"kind": "insights", "version": version, "insights": ins}
    buf = io.BytesIO()
    joblib.dump(bundle, buf)
    g = SPEC["gates"]
    gates = [
        gate("data", ins["sessions"] >= g["min_sessions"], f"{ins['sessions']} sessions, {ins['period']}"),
        gate("privacy", not leaks, "no ids, emails or postcodes in the output" if not leaks else f"found: {leaks}"),
        gate("works", len(buf.getvalue()) / 1e6 <= g["max_package_mb"], f"{len(buf.getvalue()) / 1e6:.2f} MB"),
    ]
    failed = [x["gate"] for x in gates if x["status"] == "fail"]
    report = {
        "kind": "funnel_run",
        "version": version,
        "pull_id": pull_dir.name,
        "market_id": "-",
        "created_at": now.isoformat(timespec="seconds"),
        "split": {"train": 0, "test": ins["sessions"], "cutoff": now.isoformat()},
        "judge": {"verdict": "fail" if failed else "pass", "failed": failed, "gates": gates, "gain": None},
        "challenger": {
            "model": "session funnel",
            "params": {},
            "features": "spec.yaml",
            "threshold": None,
            "test": {"balanced_accuracy": None, "roc_auc": None, "brier": None, "n": ins["sessions"]},
        },
        "champion": {"model": "none", "test": {"balanced_accuracy": None, "roc_auc": None, "brier": None, "n": 0}},
        "top_complaints": [f"{c['channel']}: {c['visit']} visits, {c['ordered']} orders" for c in ins["channels"][:5]],
    }
    out = runs_dir / f"{now:%Y%m%dT%H%M%SZ}_funnel"
    out.mkdir(parents=True)
    (out / "model.joblib").write_bytes(buf.getvalue())
    (out / "insights.json").write_text(json.dumps(ins, indent=1, ensure_ascii=False, default=str))
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    return out
