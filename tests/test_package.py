import io
import json
import tarfile
from pathlib import Path

import joblib
import pytest

from intel.package import build_package, rsync_command, verify_package

REPO = Path(__file__).resolve().parents[1]


def fake_run(tmp_path: Path, verdict: str) -> Path:
    run = tmp_path / "20261007T000000Z_content_scorer"
    run.mkdir()
    joblib.dump({"version": "cs-test", "threshold": 0.5}, run / "model.joblib")
    score = {"balanced_accuracy": 0.6, "roc_auc": 0.7, "brier": 0.2, "n": 75}
    gates = [{"gate": "better", "status": verdict, "detail": "x"}]
    (run / "report.json").write_text(
        json.dumps(
            {
                "kind": "content_scorer_run",
                "version": "cs-test",
                "pull_id": "P",
                "market_id": "M",
                "split": {"train": 1, "test": 1},
                "judge": {"verdict": verdict, "gates": gates},
                "challenger": {
                    "model": "logreg_x",
                    "params": {"C": 1},
                    "features": "all",
                    "threshold": 0.5,
                    "test": score,
                },
                "champion": {"model": "harness", "test": score},
            }
        )
    )
    return run


def test_refuses_failed_run_unless_pipeline_test(tmp_path):
    run = fake_run(tmp_path, "fail")
    with pytest.raises(ValueError, match="refusing"):
        build_package(run, tmp_path / "pkg", REPO)
    tgz = build_package(run, tmp_path / "pkg", REPO, allow_fail=True)
    with tarfile.open(tgz) as t:
        meta = json.load(t.extractfile("content_scorer-cs-test/metadata.json"))
    assert meta["purpose"] == "pipeline-test"
    assert "models/content_scorer/features.py" in meta["code"]["hashes"]


def test_build_verify_and_detect_tampering(tmp_path):
    tgz = build_package(fake_run(tmp_path, "pass"), tmp_path / "pkg", REPO)
    assert verify_package(tgz) == []
    # rebuild the archive with one changed file but the original SHA256SUMS
    with tarfile.open(tgz) as t:
        files = {m.name: t.extractfile(m).read() for m in t.getmembers() if m.isfile()}
    files["content_scorer-cs-test/model_card.md"] = b"edited"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for n, b in files.items():
            info = tarfile.TarInfo(n)
            info.size = len(b)
            t.addfile(info, io.BytesIO(b))
    tgz.write_bytes(buf.getvalue())
    problems = verify_package(tgz)
    assert "outer checksum missing or wrong" in problems and "checksum mismatch model_card.md" in problems


def test_rejects_path_traversal(tmp_path):
    tgz = tmp_path / "evil.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        info = tarfile.TarInfo("x/../../etc/passwd")
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))
    assert "unsafe paths or links in archive" in verify_package(tgz)


def test_rsync_verify_is_dry_run_checksum():
    cmd = rsync_command([Path("a.tar.gz")], "intelinbox@h", Path("/k"), dry_run_checksum=True)
    assert "--dry-run" in cmd and "--checksum" in cmd and cmd[-1] == "intelinbox@h:"
