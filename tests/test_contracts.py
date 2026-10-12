"""Consumer contract tests (Phase 5 plan §6, ADR 0033): what this repo sends to and reads from the other projects must
match their pinned contracts (contracts/pinned/, refreshed by `intel contracts sync`). A change in reels or
dk-publishing that breaks us fails here, before any run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from intel import contracts
from tests.test_lab_handover import CFG, NOW, SPEC, H, reel, slides
from tests.test_lab_media import AT, FACTS, SLIDES, VIDEO, M, finalize


def validator(name: str, ref: str | None = None) -> Draft202012Validator:
    doc = contracts.pinned(name)
    if ref is None:
        return Draft202012Validator(doc)
    return Draft202012Validator({"$ref": f"#/components/schemas/{ref}", "components": doc["components"]})


def errors(v: Draft202012Validator, instance: Any) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in v.iter_errors(instance)][:5]


# ---------- the registry agrees with the code and the pinned documents ----------


def test_every_contract_is_registered_pinned_and_spoken_at_the_same_version():
    reg = contracts.registry()
    assert {"reels.content_spec", "reels.manifest", "publishing.api", "publishing.spec", "dk_intel.tools"} <= set(reg)
    for name, entry in reg.items():
        if "pinned" in entry:
            assert (contracts.repo_root() / entry["pinned"]).exists(), f"{name}: run `intel contracts sync`"
    spec_schema = contracts.pinned("reels.content_spec")
    manifest_schema = contracts.pinned("reels.manifest")
    assert spec_schema["properties"]["spec_version"]["const"] == reg["reels.content_spec"]["version"]
    assert manifest_schema["properties"]["manifest_version"]["const"] == reg["reels.manifest"]["version"]
    assert contracts.pinned("publishing.api")["info"]["version"] == reg["publishing.api"]["version"]
    # the code speaks exactly the registered versions
    assert reg["reels.content_spec"]["version"] == M.CONTENT_SPEC_VERSION
    assert M.MANIFEST_VERSION == H.SUPPORTED_MANIFEST == reg["reels.manifest"]["version"]
    assert H.SUPPORTED_SPEC == reg["publishing.spec"]["version"] == SPEC["spec"]


# ---------- what we send to reels ----------


@pytest.mark.parametrize(
    ("content", "fmt"),
    [(VIDEO, "reel"), (SLIDES, "carousel"), (SLIDES, "image")],
    ids=["reel", "carousel", "image"],
)
def test_every_spec_the_lab_writes_is_a_valid_content_spec(content, fmt):
    spec, why = finalize(content, fmt)
    assert spec is not None, why
    assert errors(validator("reels.content_spec"), spec) == []


def test_a_price_slide_with_checked_rows_is_a_valid_content_spec():
    price = {"kind": "slides", "slides": [{"template": "price", "title": "Order"}, {"template": "card"}]}
    spec, _ = M.finalize_spec({"spec": {"content": price}}, jid="2026w42-ig-tue1700-s1", platform="instagram",
                              slot_format="carousel", planned_posted_at=AT, caption="c", avoid=[], week_label="w",
                              prices=M.price_items(FACTS))  # fmt: skip
    assert errors(validator("reels.content_spec"), spec) == []
    assert spec["content"]["slides"][0]["items"]


# ---------- what we read from reels ----------


def test_every_manifest_the_handover_is_tested_with_is_a_real_manifest(tmp_path):
    v = validator("reels.manifest")
    for manifest, _ in (reel(tmp_path), slides(tmp_path, 3), slides(tmp_path, 1, job="wk42-one", size="album")):
        assert errors(v, manifest) == []


def test_a_manifest_of_another_version_is_held_not_guessed(tmp_path):
    manifest, _ = reel(tmp_path, manifest_version=2)
    post, held = H.plan_post(manifest, {}, SPEC, CFG, NOW)
    assert post is None and [h.code for h in held] == ["contract"]


# ---------- what we send to dk-publishing ----------


def test_every_batch_the_handover_sends_is_valid_for_the_publishing_api(tmp_path):
    v = validator("publishing.api", "BatchIn")
    for manifest, _folder in (reel(tmp_path), slides(tmp_path, 3)):
        ids = {f["path"]: f"m-{i}" for i, f in enumerate(manifest["files"])}
        post, held = H.plan_post(manifest, ids, SPEC, CFG, NOW)
        assert post is not None, held
        for dry_run in (True, False):
            assert errors(v, {"posts": [post], "dry_run": dry_run}) == []


def test_a_publisher_speaking_another_contract_is_refused():
    import httpx

    other = {**SPEC, "spec": "publishing-spec/v2"}
    client = H.PublishClient(
        "https://publish.example.test", "dkp_SECRETSECRETSECRET",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=other)),
    )  # fmt: skip
    with pytest.raises(H.PublishError, match="contracts sync"):
        client.spec()


# ---------- sync itself ----------


def test_sync_reports_drift_and_refreshes_only_when_asked(tmp_path, monkeypatch):
    reels = tmp_path / "reels"
    (reels / "index").mkdir(parents=True)
    (reels / "index" / "a.json").write_text(json.dumps({"v": 1}))
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "contracts:\n"
        "  r: {source: {repo: reels, file: index/a.json}, pinned: pinned/a.json, version: 1}\n"
        "  p: {source: {url: /openapi.json}, pinned: pinned/p.json, version: '1.0'}\n"
        "  rt: {source: {runtime: x}, version: v1}\n"
    )
    monkeypatch.setattr(contracts, "repo_root", lambda: tmp_path)

    def fetch(url: str) -> Any:
        assert url == "https://publish.example.test/openapi.json"
        raise OSError("offline")

    env = {"DK_REELS_DIR": str(reels), "DK_PUBLISH_URL": "https://publish.example.test/"}
    first = {d.name: d.status for d in contracts.sync(check=True, env=env, fetch=fetch, path=registry)}
    assert first == {"r": "new", "p": "unavailable", "rt": "runtime"} and not (tmp_path / "pinned").exists()
    contracts.sync(check=False, env=env, fetch=fetch, path=registry)
    assert json.loads((tmp_path / "pinned" / "a.json").read_text()) == {"v": 1}
    assert {d.name: d.status for d in contracts.sync(check=True, env=env, fetch=fetch, path=registry)}["r"] == "same"
    (reels / "index" / "a.json").write_text(json.dumps({"v": 2}))
    assert {d.name: d.status for d in contracts.sync(check=True, env=env, fetch=fetch, path=registry)}["r"] == "changed"
    assert Path(tmp_path / "pinned" / "a.json").read_text().count('"v": 1') == 1  # check never writes
