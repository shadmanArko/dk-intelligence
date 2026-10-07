import datetime as dt

import pandas as pd
import pytest

from intel.judge import judge, paired_gain_ci
from intel.models.content_scorer.features import ALL, MarketContext, build_features, fold
from intel.models.content_scorer.predict import predict
from intel.models.content_scorer.train import FEATURE_SETS, preprocessor

GATES = {
    "min_gain_balanced_accuracy": 0.02,
    "max_platform_drop": 0.03,
    "max_brier_increase": 0.005,
    "min_live_posts": 30,
    "max_package_mb": 50,
    "max_latency_ms": 200,
}


@pytest.fixture
def ctx():
    return MarketContext(
        cues={"halal": ["halal", "হালাল"], "opening_news": ["new menu", "neu"], "price_value": ["preis", "euro"]},
        cue_lift={"halal": 0.1, "opening_news": 0.2, "price_value": -0.05},
        feast_days=["2026-05-27", "2026-12-25"],
    )


def posts(*captions, when="2026-05-20 15:00"):
    return pd.DataFrame(
        {
            "platform": ["instagram"] * len(captions),
            "content_type": ["reel"] * len(captions),
            "posted_at": [pd.Timestamp(when, tz="UTC")] * len(captions),
            "caption": list(captions),
        }
    )


def test_fold_matches_market_tagger():
    assert fold("Größe Café ı") == "groesse cafe i"


def test_cue_matching_phrases_and_bengali(ctx):
    assert ctx.match_cues("Our NEW menu is here, 100% Halal!") == {"opening_news", "halal"}
    assert ctx.match_cues("হালাল কাচ্চি") == {"halal"}
    assert ctx.match_cues("menu new") == set()  # phrase order matters


def test_features_shape_and_values(ctx):
    X = build_features(posts("Kacchi 🍛🔥 for 15,00 €?\nOrder now #berlin #kacchi", None), ctx)
    assert list(X.columns) == ALL and len(X) == 2
    r = X.iloc[0]
    assert r.emoji_count == 2 and r.has_price and r.asks_question and r.hashtag_band == "1-3"
    assert r.line_count == 2 and r.days_to_feast == 7 and r.days_since_feast == 30
    assert X.iloc[1].caption_length == 0 and not X.iloc[1].asks_question


def test_market_lift_is_mean_of_matched(ctx):
    X = build_features(posts("halal new menu", "nothing here"), ctx)
    assert X.market_cue_lift.tolist() == [pytest.approx(0.15), 0.0]


def test_context_round_trip(ctx):
    assert MarketContext.from_dict(ctx.to_dict()) == ctx


def test_predict_bundle(ctx):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    cols = FEATURE_SETS["champion_plus_text"]
    df = pd.concat(
        [posts("a" * n, when=f"2026-06-{d:02d} 1{d % 10}:00") for n, d in zip(range(1, 21), range(1, 21), strict=True)]
    )
    X = build_features(df, ctx)[cols]
    pipe = Pipeline([("pre", preprocessor(cols)), ("clf", LogisticRegression())]).fit(X, [0, 1] * 10)
    out = predict(
        {"version": "t", "pipeline": pipe, "columns": cols, "threshold": 0.5, "context": ctx.to_dict()}, df.head(2)
    )
    assert len(out) == 2 and 0 <= out[0]["probability"] <= 1 and len(out[0]["top_reasons"]) == 3


def _s(ba, fb, ig, auc=0.7, brier=0.2):
    return {
        "balanced_accuracy": ba,
        "roc_auc": auc,
        "brier": brier,
        "per_platform": {"facebook": {"balanced_accuracy": fb}, "instagram": {"balanced_accuracy": ig}},
    }


def _judge(champ, chall, live=0):
    return judge(
        champ,
        chall,
        GATES,
        data_ok=(True, ""),
        leakage_ok=(True, ""),
        live=(None, "pending") if live < 30 else (True, "ok"),
        size_mb=0.1,
        latency_ms=5,
        gain_ci=(0.0, 0.1),
    )


def test_judge_passes_clear_winner_and_waits_for_live_evidence():
    v = _judge(_s(0.60, 0.60, 0.60), _s(0.65, 0.64, 0.62, auc=0.75, brier=0.19))
    assert v["verdict"] == "pass"
    assert {g["gate"]: g["status"] for g in v["gates"]}["live_evidence"] == "pending"


@pytest.mark.parametrize(
    "chall,failed",
    [
        (_s(0.61, 0.61, 0.61, auc=0.75), "better"),  # gain below +0.02
        (_s(0.65, 0.70, 0.55, auc=0.75), "no_platform_worse"),  # instagram drops 0.05
        (_s(0.65, 0.65, 0.65, auc=0.75, brier=0.25), "calibrated"),
        (_s(0.65, 0.65, 0.65, auc=0.65), "better"),  # AUC worse
    ],
)
def test_judge_failures(chall, failed):
    assert failed in _judge(_s(0.60, 0.60, 0.60), chall)["failed"]


def test_paired_gain_ci_identical_models_is_zero():
    y = [0, 1] * 20
    p = [0, 1, 1, 0] * 10
    assert paired_gain_ci(y, p, p) == (0.0, 0.0)


def test_feast_days_use_berlin_date(ctx):
    late = posts("x", when="2026-05-26 23:30")  # 01:30 on 27 May in Berlin
    assert build_features(late, ctx).days_to_feast.iloc[0] == 0
    assert dt.date(2026, 5, 27).isoformat() in ctx.feast_days


def test_live_gate_uses_only_logged_predictions_with_7d_labels():
    from intel.judge import live_gate

    test = pd.DataFrame({"id": [f"p{i}" for i in range(40)]})
    rows = [
        {
            "post_id": f"p{i}",
            "model_name": "content_scorer",
            "predicted_label": 0,
            "outcomes": {"7d": {"label": 1 if i % 2 else 0}},
        }
        for i in range(35)
    ]
    rows += [
        {"post_id": "p0", "model_name": "harness_predictor", "predicted_label": 1, "outcomes": {"7d": {"label": 0}}}
    ]
    status, detail = live_gate(test, [1 if i % 2 else 0 for i in range(40)], rows, 30)
    assert status is True and "on 35 live posts" in detail and "100%" in detail
    assert live_gate(test, [0] * 40, rows[:10], 30)[0] is None
    assert live_gate(test, [0] * 40, None, 30) == (None, "0 test posts have live 7-day results (need 30)")
