"""Funnel: source classification, sessions/steps, order matching, friction, per-post window, privacy gate, page."""

import json

import pandas as pd
import pytest

from intel.models.funnel.run import PRIVATE, all_strings, channel, match_orders, per_post, sessions, summarise


@pytest.mark.parametrize(
    "utm,ref,expected",
    [
        (None, "l.facebook.com", "facebook"),
        (None, "l.instagram.com", "instagram"),
        (None, "l.wl.co", "whatsapp"),
        (None, "www.google.com", "google"),
        (None, "$direct", "direct / unknown"),
        (None, None, "direct / unknown"),
        ("instagram", "facebook.com", "instagram"),  # an explicit UTM tag wins over the referrer
        (None, "some-blog.de", "other site"),
    ],
)
def test_channel(utm, ref, expected):
    assert channel(utm, ref) == expected


def ev(sid, ts, event, host="dhakakacchi.com", **props):
    p = {"$session_id": sid, "$host": host, "$pathname": props.pop("path", "/"), **props}
    return {"occurred_at": pd.Timestamp(ts, tz="UTC"), "payload": json.dumps({"event": event, "properties": p})}


@pytest.fixture
def pull(tmp_path):
    rows = [
        ev("s1", "2026-10-01 10:00", "$pageview", **{"$session_entry_referring_domain": "l.facebook.com"}),
        ev("s1", "2026-10-01 10:01", "$pageview", path="/order"),
        ev("s1", "2026-10-01 10:02", "order_cart_started"),
        ev("s1", "2026-10-01 10:03", "order_checkout_clicked"),
        ev("s1", "2026-10-01 10:04", "order_submitted", totalCents=2800),
        ev("s2", "2026-10-01 11:00", "$pageview", **{"$session_entry_referring_domain": "$direct"}),
        ev("s2", "2026-10-01 11:01", "order_checkout_clicked"),
        ev("s2", "2026-10-01 11:02", "auth_registration_failed"),
        ev("s3", "2026-10-01 12:00", "order_submitted", host="localhost:4196", totalCents=1600),  # test traffic
        ev("s4", "2026-10-02 09:00", "$pageview", **{"$session_entry_referring_domain": "l.facebook.com"}),
    ]
    pd.DataFrame(rows).to_parquet(tmp_path / "raw_posthog_events.parquet")
    orders = [
        {
            "id": "o1",
            "created_at": "2026-10-01T10:04:30Z",
            "subtotal_cents": 2100,
            "delivery_fee_cents": 700,
            "status": "delivered",
        },
        {
            "id": "o2",
            "created_at": "2026-10-01T10:04:10Z",
            "subtotal_cents": 999,
            "delivery_fee_cents": 0,
            "status": "cancelled",
        },
    ]
    pd.DataFrame({"payload": [json.dumps(o) for o in orders]}).to_parquet(tmp_path / "raw_orders_direct.parquet")
    pd.DataFrame(
        {
            "id": ["p1", "p2"],
            "platform": ["facebook", "facebook"],
            "posted_at": pd.to_datetime(["2026-10-01 09:30", "2026-10-01 23:00"], utc=True),
            "content_type": ["video", None],
            "caption": ["Kacchi Saturday", None],
        }
    ).to_parquet(tmp_path / "social_post.parquet")
    pd.DataFrame(columns=["id"]).to_parquet(tmp_path / "tracked_link.parquet")
    return tmp_path


def test_sessions_steps_orders_friction(pull):
    s = match_orders(sessions(pull), pull).set_index("sid")
    assert set(s.index) == {"s1", "s2", "s4"}  # localhost excluded
    assert s.loc["s1", "channel"] == "facebook" and bool(s.loc["s1", "ordered"])  # same total 2800 within 3 min
    assert s.loc["s1", ["menu", "cart", "checkout", "submitted"]].all()
    ins = summarise(s.reset_index(), pull)
    assert ins["totals"]["ordered"] == 1 and ins["orders_by_channel"] == {"facebook": 1}
    assert ins["checkout_friction"]["stuck_sessions"] == 1
    assert ins["checkout_friction"]["with"]["registration failed"] == 1
    assert any("link builder" in a.lower() for a in ins["advice"])
    assert not any("reached checkout" in a for a in ins["advice"])  # 1 of 2 submitted = 50%: warning only below 40%
    assert not [x for x in all_strings({k: v for k, v in ins.items() if k != "advice"}) if PRIVATE.search(x)]


def test_post_window_is_cut_at_next_post(pull):
    s = match_orders(sessions(pull), pull)
    posts = {p["posted_at"][:13]: p for p in per_post(s, pull)}
    assert posts["2026-10-01T09"]["sessions"] == 1 and posts["2026-10-01T09"]["orders"] == 1  # s4 belongs to p2
    assert posts["2026-10-01T23"]["sessions"] == 1


@pytest.mark.parametrize(
    "text,leak",
    [
        ("4f2c0a1e-1b2c-4d3e-8f90-123456789abc", True),
        ("mail me@shop.de", True),
        ("13353", True),
        ("facebook: 13 visits", False),
        ("2026-10-01", False),
    ],
)
def test_privacy_pattern(text, leak):
    assert bool(PRIVATE.search(text)) == leak


def test_funnel_page(pull):
    from intel.serve.views import funnel_html

    ins = summarise(match_orders(sessions(pull), pull), pull)
    page = funnel_html({"version": "fn-1", "insights": ins})
    assert "from visit to order" in page and "Stuck at checkout" in page and "registration failed" in page
