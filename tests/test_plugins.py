"""The model plug-in contract (ADR 0009): discovery, validation, and that a new model needs no other file changes."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import pytest

from intel.pipeline import definition, edges, nodes, steps
from intel.plugins import Box, ModelPlugin, PluginError, RunContext, StepOutcome, discover, model_sources, validate


def test_discovery_finds_every_model_in_run_order():
    ps = discover()
    assert [p.step for p in ps] == ["train", "mine", "community", "occasions", "funnel", "creators"]
    assert set(model_sources()) == {
        "content_scorer",
        "reach_scorer",
        "weakness_miner",
        "community_map",
        "occasion_planner",
        "funnel",
        "creator_matcher",
    }
    assert all(isinstance(p, ModelPlugin) for p in ps)


def _report(name: str, verdict: str) -> dict[str, Any]:
    return {
        "kind": f"{name}_run",
        "version": f"{name}-v1",
        "judge": {"verdict": verdict, "gates": [{"gate": "better", "status": verdict, "detail": "d"}], "failed": []},
        "challenger": {"test": {"balanced_accuracy": 0.61}},
        "champion": {"test": {"balanced_accuracy": 0.6}},
    }


class Dummy(ModelPlugin):
    """A ninth model, defined only here: nothing else in the code base knows it."""

    step = "dummy"
    order = 99
    label = "Dummy model"
    desc = "A model added by a test."
    inputs = ("market", "pull")
    box = Box(col=4, row=2, label="Dummy", sub="test only")
    kind = "insights"
    models = {"dummy": ("models/funnel/spec.yaml",)}

    def __init__(self, verdict: str = "fail", boom: bool = False):
        self.verdict, self.boom = verdict, boom
        self.seen: list[RunContext] = []

    def run(self, ctx: RunContext) -> StepOutcome:
        self.seen.append(ctx)
        if self.boom:
            raise RuntimeError("dummy exploded")
        d = ctx.runs_dir / "dummy"
        d.mkdir(parents=True)
        (d / "report.json").write_text(json.dumps(_report("dummy", self.verdict)))
        return StepOutcome([d], "dummy done", {"rows": 3})

    def scores(self, report: dict[str, Any]) -> tuple[float | None, float | None]:
        return 0.61, 0.6


def test_a_new_plugin_appears_in_the_run_and_the_diagram():
    ps = (*discover(), Dummy())
    st = list(steps(ps))
    assert st.index("dummy") == st.index("judge") - 1  # model steps run before the judge
    assert {"id": "dummy", "label": "Dummy", "sub": "test only"}.items() <= next(
        n for n in nodes(ps) if n["id"] == "dummy"
    ).items()
    assert {("market", "dummy"), ("pull", "dummy"), ("dummy", "judge")} <= set(edges(ps))
    d = definition(ps)
    ids = {n["id"] for n in d["nodes"]}
    assert all(a in ids and b in ids for a, b in d["edges"])
    assert len({(n["col"], n["row"]) for n in d["nodes"]}) == len(d["nodes"])  # no two boxes on one spot
    assert {n["step"] for n in d["nodes"] if n["step"]} == set(steps(ps))  # every step has a box


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"step": "funnel"}, "duplicate plug-in step"),
        ({"order": 50}, "duplicate plug-in order"),
        ({"models": {"funnel": ("models/funnel/spec.yaml",)}}, "duplicate plug-in model name"),
        ({"models": {"dummy": ("models/dummy/nope.py",)}}, "source files not found"),
        ({"inputs": ("orders",)}, "unknown inputs"),
        ({"models": {}}, "produces no model"),
    ],
)
def test_validation_fails_fast(change, message):
    bad = type("Bad", (Dummy,), change)()
    with pytest.raises(PluginError, match=message):
        validate((*discover(), bad))


def _fake_snapshots(tmp_path: Path, monkeypatch) -> None:
    import intel.market
    import intel.pull
    import intel.tunnel
    import intel.weekly as w

    def snap(kind: str, tables: dict[str, Any]) -> Path:
        d = tmp_path / kind / "snap-1"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"tables": tables, "total_rows": 7}))
        return d

    pull = snap("pull", {"social_post": {"rows": 7}})
    market = snap("market", {})
    monkeypatch.setattr(w, "mip_refresh_running", lambda: False)
    monkeypatch.setattr(intel.tunnel, "warehouse_tunnel", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(intel.pull, "run_pull", lambda *a, **k: pull)
    monkeypatch.setattr(intel.pull, "verify_pull", lambda p: [])
    monkeypatch.setattr(intel.market, "run_market_pull", lambda *a, **k: market)


def test_weekly_run_end_to_end_with_only_a_new_plugin(tmp_path, monkeypatch):
    import intel.weekly as w
    from intel.settings import Settings

    _fake_snapshots(tmp_path, monkeypatch)
    s = Settings(
        _env_file=None,
        intel_data_dir=tmp_path / "data",
        dk_warehouse_dsn="postgresql://u:p@127.0.0.1:1/x",
        mip_reader_dsn="postgresql://r@x/mip",
    )
    dummy = Dummy(verdict="fail")
    r = w.run_weekly(s, upload=False, log=lambda *_: None, plugins=[dummy])
    assert r["status"] == "fail" and r["failed_step"] is None  # ran to the end; nothing better -> nothing packaged
    assert r["models"] == [
        {"name": "dummy", "version": "dummy-v1", "verdict": "fail", "new": 0.61, "current": 0.6, "failed_gates": []}
    ]
    ctx = dummy.seen[0]
    assert ctx.pull.name == ctx.market.name == "snap-1" and ctx.ledger is None  # no API URL -> no ledger
    st = json.loads((tmp_path / "data" / "weekly" / "current.json").read_text())
    assert list(st["steps"]) == list(steps([dummy]))  # progress shows exactly this run's steps
    assert st["steps"]["dummy"]["status"] == "done" and st["steps"]["dummy"]["details"] == {"rows": 3}
    assert st["steps"]["package"]["status"] == "skipped"


def test_a_failing_plugin_is_named_in_the_error_report(tmp_path, monkeypatch):
    import intel.weekly as w
    from intel.settings import Settings

    _fake_snapshots(tmp_path, monkeypatch)
    s = Settings(
        _env_file=None,
        intel_data_dir=tmp_path / "data",
        dk_warehouse_dsn="postgresql://u:p@127.0.0.1:1/x",
        mip_reader_dsn="postgresql://r@x/mip",
    )
    r = w.run_weekly(s, upload=False, log=lambda *_: None, plugins=[Dummy(boom=True)])
    assert r["status"] == "error" and r["failed_step"] == "dummy" and "dummy exploded" in r["error"]


def test_telegram_text_comes_from_the_plugin():
    from intel.serve.telegram import approval_text

    meta = {"kind": "insights", "name": "funnel", "version": "fn-1", "top_complaints": ["12 visits"]}
    assert "Visit-to-order funnel" in approval_text(meta, 1, "https://x.ts.net")
    unknown = {**meta, "name": "something_new"}
    assert "something_new" in approval_text(unknown, 1, None)  # a model without its own text still gets one


# ---------- discovery against a fake models package ----------

_PLUGIN_SRC = """
from intel.plugins import Box, ModelPlugin, StepOutcome


class P(ModelPlugin):
    step, order, label, desc = "{name}", {order}, "{name}", "a test model"
    inputs, kind = ("pull",), "insights"
    box = Box(col=9, row={order}, label="{name}")
    models = {{"{name}": ("models/funnel/spec.yaml",)}}

    def run(self, ctx):
        return StepOutcome([], "", {{}})


PLUGIN = P()
"""


def _fake_package(tmp_path: Path, monkeypatch, name: str, layout: dict[str, str]) -> str:
    """layout: relative file path -> content, under a fresh package `name` on sys.path."""
    root = tmp_path / name
    (root).mkdir()
    (root / "__init__.py").write_text("")
    for rel, text in layout.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        (f.parent / "__init__.py").touch()
        f.write_text(text)
    monkeypatch.syspath_prepend(str(tmp_path))
    return name


def test_find_plugins_orders_skips_helpers_and_uses_defaults(tmp_path, monkeypatch):
    from intel.plugins import find_plugins

    pkg = _fake_package(
        tmp_path,
        monkeypatch,
        "fake_models_ok",
        {
            "b/plugin.py": _PLUGIN_SRC.format(name="b", order=2),
            "a/plugin.py": _PLUGIN_SRC.format(name="a", order=1),
            "a/spec.yaml": "",
            "shared/util.py": "X = 1\n",  # a helper package without a plug-in is fine
        },
    )
    ps = find_plugins(pkg)
    assert [p.step for p in ps] == ["a", "b"]
    assert ps[0].scores({}) == (None, None) and ps[0].approval_text({}, 1, None) is None  # base-class defaults


@pytest.mark.parametrize(
    ("layout", "error"),
    [
        ({"x/plugin.py": "PLUGIN = 42\n"}, "must define PLUGIN"),
        ({"x/plugin.py": _PLUGIN_SRC.format(name="x", order=1), "y/spec.yaml": ""}, "no plug-in producing them"),
        ({"x/plugin.py": "import a_module_that_does_not_exist\n"}, "a_module_that_does_not_exist"),
    ],
    ids=["not-a-plugin", "orphan-model-folder", "broken-import"],
)
def test_find_plugins_fails_loudly(tmp_path, monkeypatch, layout, error):
    from intel.plugins import find_plugins

    pkg = _fake_package(tmp_path, monkeypatch, f"fake_models_{abs(hash(error))}", layout)
    with pytest.raises(Exception, match=error):
        find_plugins(pkg)
