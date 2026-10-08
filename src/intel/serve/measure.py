"""M6 measurement: shadow-score every new real post, then record what really happened.

Runs inside intel-api every INTEL_MEASURE_INTERVAL_S (default 1 h). Reads the warehouse with the read-only
`intelligence_reader` role over the harness docker network; writes only to intel-db.

1. Shadow scoring: each FB/IG post published after INTEL_LEDGER_START gets exactly one prediction per model:
   - `content_scorer`: the active approved package, computed here;
   - `harness_predictor`: the production predictor, called over the network with the same request.
   Both models are frozen and only see pre-posting inputs, so scoring after publication cannot leak the outcome.
2. Outcomes at 24h / 72h / 7d: the first metrics snapshot within LATE after the horizon (real age stored).
   label = engagement > median latest engagement of EARLIER posts on the same platform (the training label
   compares with an expanding median that includes the post itself; using earlier posts only is the honest live
   version, since the post's own final number is not known yet).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass

import httpx
import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import dict_row
from sklearn.metrics import balanced_accuracy_score

from intel.datasets.content import REACH_MIN_PRIOR, REACH_WINDOW
from intel.models.content_scorer.predict import predict as cs_predict
from intel.serve.db import connect, one

log = logging.getLogger("intel.measure")
HORIZONS = {"24h": dt.timedelta(hours=24), "72h": dt.timedelta(hours=72), "7d": dt.timedelta(days=7)}
# A horizon only accepts a snapshot taken within this window after it (otherwise "24h" could silently be a 7-day
# number). Snapshots are history in the warehouse, so they can be recorded late; we keep checking for GIVE_UP more.
LATE = {"24h": dt.timedelta(hours=12), "72h": dt.timedelta(hours=24), "7d": dt.timedelta(hours=48)}
GIVE_UP = dt.timedelta(days=7)
PLATFORMS = ("facebook", "instagram")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def input_hash(platform: str, content_type: str | None, planned_at: dt.datetime, caption: str) -> str:
    return sha(json.dumps([platform, content_type, planned_at.astimezone(dt.UTC).isoformat(), caption]))


@dataclass
class RunResult:
    new_posts: int = 0
    predictions: int = 0
    outcomes: int = 0
    errors: int = 0


class Measurer:
    def __init__(
        self,
        intel_dsn: str,
        warehouse_dsn: str,
        ledger_start: dt.datetime,
        models,
        harness_predictor_url: str | None = None,
        http: httpx.Client | None = None,
    ):
        self.intel, self.wh, self.start, self.models = intel_dsn, warehouse_dsn, ledger_start, models
        self.harness_url = harness_predictor_url
        self.http = http or httpx.Client(timeout=15)

    def _wh(self):
        conn = psycopg.connect(self.wh, row_factory=dict_row, connect_timeout=15, application_name="intel-measure")
        conn.read_only = True
        return conn

    # ---------- 1. shadow scoring ----------
    def _new_posts(self) -> list[dict]:
        with connect(self.intel) as c:
            known = {r["post_id"] for r in c.execute("select post_id from ledger_post")}
        with self._wh() as w:
            rows = w.execute(
                """select id, platform, posted_at, content_type, coalesce(caption, '') as caption from social_post
                   where platform = any(%s) and posted_at >= %s order by posted_at""",
                (list(PLATFORMS), self.start),
            ).fetchall()
        return [r for r in rows if r["id"] not in known]

    def _harness(self, p: dict) -> dict | None:
        if not self.harness_url:
            return None
        body = {
            "platform": p["platform"],
            "content_type": p["content_type"],
            "caption": p["caption"][:5000],
            "planned_posted_at": p["posted_at"].isoformat(),
        }
        r = self.http.post(f"{self.harness_url}/v1/predict", json=body, headers={"x-request-id": f"intel-{p['id']}"})
        r.raise_for_status()
        d = r.json()
        return {
            "model_name": "harness_predictor",
            "model_version": d["model_version"],
            "probability": d["probability"],
            "threshold": d["threshold"],
            "predicted_label": int(d["probability"] >= d["threshold"]),
            "top_reasons": d.get("top_reasons"),
        }

    def _active_names(self) -> list[str]:
        names = getattr(self.models, "names", None)
        return names() if names else ["content_scorer"]

    def _scorers(self, p: dict) -> list[dict]:
        """One prediction per ACTIVE approved model (content_scorer, reach_scorer, ...)."""
        out = []
        row = pd.DataFrame([{k: p[k] for k in ("platform", "content_type", "caption")} | {"posted_at": p["posted_at"]}])
        for name in self._active_names():
            m = self.models.get(name)
            if m is None:
                continue
            r = cs_predict(m["bundle"], row)[0]
            out.append(
                {
                    "model_name": name,
                    "model_version": r["model_version"],
                    "probability": r["probability"],
                    "threshold": r["threshold"],
                    "predicted_label": r["label"],
                    "top_reasons": r["top_reasons"],
                }
            )
        return out

    def _harness_list(self, p: dict) -> list[dict]:
        x = self._harness(p)
        return [x] if x else []

    def score_new_posts(self, res: RunResult) -> None:
        for p in self._new_posts():
            preds = []
            for fn in (self._scorers, self._harness_list):
                try:
                    preds.extend(fn(p))
                except Exception:
                    res.errors += 1
                    log.exception("scoring post %s with %s failed", p["id"], fn.__name__)
            if not preds:
                continue  # retry next run (no model reachable yet)
            h = input_hash(p["platform"], p["content_type"], p["posted_at"], p["caption"])
            with connect(self.intel) as c, c.transaction():
                c.execute(
                    """insert into ledger_post (post_id, platform, posted_at, content_type, caption_sha256,
                             caption_length) values (%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                    (p["id"], p["platform"], p["posted_at"], p["content_type"], sha(p["caption"]), len(p["caption"])),
                )
                for x in preds:
                    cur = c.execute(
                        """insert into prediction (source, model_name, model_version, post_id, platform, planned_at,
                           input_sha256, probability, threshold, predicted_label, top_reasons)
                           values ('shadow',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                        (
                            x["model_name"],
                            x["model_version"],
                            p["id"],
                            p["platform"],
                            p["posted_at"],
                            h,
                            x["probability"],
                            x["threshold"],
                            x["predicted_label"],
                            json.dumps(x["top_reasons"]),
                        ),
                    )
                    res.predictions += cur.rowcount
            res.new_posts += 1

    def backfill_missing_models(self, res: RunResult) -> None:
        """A post scored while one model was unavailable gets that model's prediction later, if still unmeasured."""
        with connect(self.intel) as c:
            expected = len(self._active_names()) + (1 if self.harness_url else 0)
            todo = c.execute(
                """select lp.post_id from ledger_post lp
                   where not exists (select 1 from post_outcome o where o.post_id = lp.post_id)
                     and (select count(distinct model_name) from prediction p
                          where p.post_id = lp.post_id and p.source = 'shadow') < %s""",
                (expected,),
            ).fetchall()
        if not todo:
            return
        with self._wh() as w:
            posts = w.execute(
                """select id, platform, posted_at, content_type, coalesce(caption,'') as caption
                                 from social_post where id = any(%s)""",
                ([t["post_id"] for t in todo],),
            ).fetchall()
        for p in posts:
            xs = []
            for fn in (self._scorers, self._harness_list):
                try:
                    xs.extend(fn(p))
                except Exception:
                    res.errors += 1
            for x in xs:
                with connect(self.intel) as c:
                    cur = c.execute(
                        """insert into prediction (source, model_name, model_version, post_id, platform, planned_at,
                           input_sha256, probability, threshold, predicted_label, top_reasons)
                           values ('shadow',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                        (
                            x["model_name"],
                            x["model_version"],
                            p["id"],
                            p["platform"],
                            p["posted_at"],
                            input_hash(p["platform"], p["content_type"], p["posted_at"], p["caption"]),
                            x["probability"],
                            x["threshold"],
                            x["predicted_label"],
                            json.dumps(x["top_reasons"]),
                        ),
                    )
                    res.predictions += cur.rowcount

    # ---------- 2. outcomes ----------
    def record_outcomes(self, res: RunResult, now: dt.datetime | None = None) -> None:
        now = now or dt.datetime.now(dt.UTC)
        with connect(self.intel) as c:
            posts = c.execute("select post_id, platform, posted_at from ledger_post").fetchall()
            done = {(r["post_id"], r["horizon"]) for r in c.execute("select post_id, horizon from post_outcome")}
        due = [
            (p, h)
            for p in posts
            for h, d in HORIZONS.items()
            if (p["post_id"], h) not in done
            and p["posted_at"] + d <= now
            and p["posted_at"] + d + LATE[h] + GIVE_UP > now
        ]
        if not due:
            return
        ids = list({p["post_id"] for p, _ in due})
        with self._wh() as w:
            snaps = w.execute(
                """select social_post_id, captured_at, impressions, reach, likes, comments, shares, saves, clicks
                   from social_metrics_snapshot where social_post_id = any(%s) order by captured_at""",
                (ids,),
            ).fetchall()
            history = w.execute(
                """select sp.platform, sp.posted_at, s.likes + s.comments + s.shares as engagement, s.reach
                   from social_post sp join lateral (
                     select likes, comments, shares, reach from social_metrics_snapshot m
                     where m.social_post_id = sp.id order by captured_at desc limit 1) s on true
                   where sp.platform = any(%s)""",
                (list(PLATFORMS),),
            ).fetchall()
        by_post: dict = {}
        for s in snaps:
            by_post.setdefault(s["social_post_id"], []).append(s)
        hist = pd.DataFrame(history, columns=["platform", "posted_at", "engagement", "reach"])
        for p, h in due:
            target = p["posted_at"] + HORIZONS[h]
            window = target + LATE[h]
            snap = next((s for s in by_post.get(p["post_id"], []) if target <= s["captured_at"] <= window), None)
            if snap is None:
                continue
            parts = [snap["likes"], snap["comments"], snap["shares"]]
            eng = None if any(v is None for v in parts) else int(sum(parts))
            earlier = hist[(hist["platform"] == p["platform"]) & (hist["posted_at"] < p["posted_at"])][
                "engagement"
            ].dropna()
            med = float(np.median(earlier.astype(float))) if len(earlier) else None
            label = None if eng is None or med is None else int(eng > med)
            # reach question (reach_scorer): beat the median latest reach of the previous REACH_WINDOW posts
            prior = hist[(hist["platform"] == p["platform"]) & (hist["posted_at"] < p["posted_at"])]
            prior = prior.sort_values("posted_at")["reach"].dropna().tail(REACH_WINDOW)
            rmed = float(np.median(prior.astype(float))) if len(prior) >= REACH_MIN_PRIOR else None
            label_reach = None if snap["reach"] is None or rmed is None else int(snap["reach"] > rmed)
            age = (snap["captured_at"] - p["posted_at"]).total_seconds() / 3600
            with connect(self.intel) as c:
                cur = c.execute(
                    """insert into post_outcome (post_id, horizon, captured_at, age_hours, impressions, reach, likes,
                       comments, shares, saves, clicks, engagement, platform_median, label, reach_median,
                       label_reach)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                    (
                        p["post_id"],
                        h,
                        snap["captured_at"],
                        round(age, 1),
                        snap["impressions"],
                        snap["reach"],
                        snap["likes"],
                        snap["comments"],
                        snap["shares"],
                        snap["saves"],
                        snap["clicks"],
                        eng,
                        med,
                        label,
                        rmed,
                        label_reach,
                    ),
                )
                res.outcomes += cur.rowcount

    def run_once(self) -> RunResult:
        res = RunResult()
        self.score_new_posts(res)
        self.backfill_missing_models(res)
        self.record_outcomes(res)
        return res


MODEL_LABEL = {"reach_scorer": "label_reach"}  # which outcome column answers each model's question


def live_summary(intel_dsn: str, horizon: str = "7d") -> dict:
    """Live accuracy per model on posts whose outcome at `horizon` is known (the judge's live-evidence gate).
    `models`: each model against its OWN question; `reach`: every model against the reach question."""
    with connect(intel_dsn) as c:
        rows = c.execute(
            """select p.model_name, p.model_version, p.predicted_label, o.label, o.label_reach
               from prediction p join post_outcome o on o.post_id = p.post_id and o.horizon = %s
               where p.source = 'shadow'""",
            (horizon,),
        ).fetchall()
        counts = one(
            c.execute(
                """select (select count(*) from ledger_post) as posts,
                      (select count(*) from prediction where source='shadow') as shadow_predictions,
                      (select count(*) from prediction where source='api') as api_predictions,
                      (select count(*) from post_outcome) as outcomes"""
            )
        )
    out = {"horizon": horizon, **counts, "models": {}, "reach": {}}
    df = pd.DataFrame(rows, columns=["model_name", "model_version", "predicted_label", "label", "label_reach"])

    def acc(g: pd.DataFrame, col: str) -> dict | None:
        g = g[g[col].notna()]
        if g.empty:
            return None
        y, yhat = g[col].astype(int), g["predicted_label"].astype(int)
        return {
            "n": len(g),
            "versions": sorted(g["model_version"].unique()),
            "accuracy": round(float((y == yhat).mean()), 4),
            "balanced_accuracy": round(float(balanced_accuracy_score(y, yhat)), 4) if y.nunique() == 2 else None,
        }

    for key, g in df.groupby("model_name"):
        name = str(key)
        if (a := acc(g, MODEL_LABEL.get(name, "label"))) is not None:
            out["models"][name] = a
        if (r := acc(g, "label_reach")) is not None:
            out["reach"][name] = r
    return out


def log_api_prediction(intel_dsn: str, body, result: dict) -> None:
    with connect(intel_dsn) as c:
        c.execute(
            """insert into prediction (source, model_name, model_version, platform, planned_at, input_sha256,
               probability, threshold, predicted_label, top_reasons)
               values ('api','content_scorer',%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                result["model_version"],
                body.platform,
                body.planned_posted_at,
                input_hash(body.platform, body.content_type, body.planned_posted_at, body.caption),
                result["probability"],
                result["threshold"],
                result["label"],
                json.dumps(result["top_reasons"]),
            ),
        )
