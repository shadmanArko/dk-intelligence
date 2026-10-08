"""M4: seal a training run into a model package, and upload it to the VPS inbox (door 2).

Package = `<name>-<version>.tar.gz` (+ `.sha256` next to it), containing:
    model.joblib        the bundle (sklearn pipeline + frozen market context + columns + threshold)
    metadata.json       versions, data ids, code hashes, git commit, judge verdict, scores
    report.json         the full judge report of the run
    model_card.md       human summary
    source/*.py         the feature + predict code it was trained with (for audit)
    SHA256SUMS          of every file above

The server (M5) only activates a package whose feature code hash equals the code it runs, so training and
serving can never silently diverge. Packages with a failed verdict are refused unless built with
`allow_fail=True`, which marks them `purpose: pipeline-test` (never offered for approval).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import platform
import subprocess
import tarfile
import zlib
from pathlib import Path
from typing import Any

import sklearn

from intel import __version__
from intel.plugins import model_sources

PKG_ROOT = Path(__file__).parent


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def code_hashes(name: str) -> dict[str, str]:
    return {rel: sha256_bytes((PKG_ROOT / rel).read_bytes()) for rel in model_sources()[name]}


def git_state(repo: Path) -> dict[str, Any]:
    def run(*a: str) -> str:
        return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=False).stdout.strip()

    return {"commit": run("rev-parse", "HEAD") or None, "dirty": bool(run("status", "--porcelain", "--", "src"))}


def _calibration(run_dir: Path | None) -> list[tuple[str, int, float, float]]:
    """(bin, n, mean predicted, observed share) over the test set, 5 bins of predicted probability."""
    f = run_dir / "test_predictions.csv" if run_dir else None
    if f is None or not f.exists():
        return []
    import pandas as pd

    tp = pd.read_csv(f)
    if tp.empty or "challenger_p" not in tp:
        return []
    bins = pd.cut(tp["challenger_p"], [0, 0.2, 0.4, 0.6, 0.8, 1.0], include_lowest=True)
    out = []
    for interval, g in tp.groupby(bins, observed=True):
        out.append(
            (
                f"{interval.left:.1f}-{interval.right:.1f}",
                len(g),
                float(g["challenger_p"].mean()),
                float(g["label"].mean()),
            )
        )
    return out


def _bullets(items: tuple[str, ...] | list[str]) -> str:
    return "\n".join(f"- {x}" for x in items) or "- (none recorded)"


def model_card(meta: dict[str, Any], run_dir: Path | None = None, quality_dir: Path | None = None) -> str:
    """Model card v2 (ADR 0019): purpose and limits (from the plug-in), lineage, data checks, training coverage,
    results with uncertainty and per platform, calibration, and the judge's gates."""
    from intel.plugins import ModelCard, plugin_for_model
    from intel.sealed import diff as sealed_diff

    t, c, j = meta["scores"]["challenger"], meta["scores"]["champion"], meta["judge"]
    plugin = plugin_for_model(meta["name"])
    card = (plugin.cards.get(meta["name"]) if plugin else None) or ModelCard(
        intended_use="(no card text recorded for this model)"
    )
    gates = "\n".join(f"| {g['gate']} | {g['status']} | {g['detail']} |" for g in j["gates"])
    sealed = [x for x in sealed_diff() if x.startswith(f"{meta['name']}:")]
    q = (quality_dir / f"warehouse-{meta['data']['pull_id']}.json") if quality_dir else None
    quality = "not recorded"
    if q is not None and q.exists():
        qd = json.loads(q.read_text())
        errs = sum(1 for f in qd["findings"] if f["severity"] == "error")
        warns = sum(1 for f in qd["findings"] if f["severity"] == "warning")
        quality = f"{len(qd['card'])} tables checked: {errs} errors, {warns} warnings (ADR 0017)"
    coverage = ""
    ref_file = run_dir / "reference.json" if run_dir else None
    if ref_file is not None and ref_file.exists():
        ref = json.loads(ref_file.read_text())
        plat = ref["features"].get("platform")
        shares = (
            ", ".join(f"{k} {v:.0%}" for k, v in zip(plat["categories"], plat["shares"], strict=False) if v)
            if plat
            else "n/a"
        )
        coverage = (
            f"\n## Training data\n{ref['n_train']} posts, positive share {ref['base_rate']:.0%}; platforms: {shares}. "
            "Anything outside this coverage is out of distribution (monitored, ADR 0018).\n"
        )
    perf = ""
    if t.get("balanced_accuracy") is not None:
        ci = t.get("balanced_accuracy_ci95")
        cm = t.get("confusion_matrix")
        per = t.get("per_platform") or {}
        rows = "\n".join(f"| {p} | {v.get('n')} | {v.get('balanced_accuracy')} |" for p, v in per.items())
        perf = f"""
## Results (newest posts, never seen in training)
| | Challenger (this) | Champion (today's model) |
|---|---|---|
| Balanced accuracy | {t["balanced_accuracy"]}{f" (95% CI {ci[0]}-{ci[1]})" if ci else ""} | {c["balanced_accuracy"]} |
| AUC | {t["roc_auc"]} | {c["roc_auc"]} |
| Brier (lower = better calibrated) | {t["brier"]} | {c["brier"]} |
| Test posts | {t["n"]} | {c["n"]} |
{f"{chr(10)}Confusion matrix (rows = actual 0/1, columns = predicted 0/1): {cm}" if cm else ""}

| Platform | Test posts | Balanced accuracy |
|---|---|---|
{rows or "| (none) | | |"}
"""
    cal = _calibration(run_dir)
    calibration = ""
    if cal:
        crow = "\n".join(f"| {b} | {n} | {m:.2f} | {o:.2f} |" for b, n, m, o in cal)
        calibration = f"""
## Calibration (test set)
When the model says X, how often does it happen?
| Predicted | Posts | Mean predicted | Observed share |
|---|---|---|---|
{crow}
"""
    m, dd, g = meta["model"], meta["data"], meta["git"]
    model_line = f"{m['family']} on feature set `{m['features']}`, threshold {m['threshold']}"
    data_line = f"warehouse pull `{dd['pull_id']}`, market snapshot `{dd['market_id']}`, split {dd['split']}"
    sealed_line = "match the lock" if not sealed else "DIFFER from the lock: " + "; ".join(sealed)
    dirty = " (uncommitted changes!)" if g["dirty"] else ""
    code_line = f"commit `{(g['commit'] or '?')[:10]}`{dirty}; sealed files {sealed_line}"
    return f"""# Model card: {meta["name"]} {meta["version"]}

**Purpose:** {meta["purpose"]} · **Verdict:** {j["verdict"].upper()} · built {meta["created_at"]}

{meta["question"]}

## Intended use
{card.intended_use}

**Users:** {card.users}

## Out of scope
{_bullets(card.out_of_scope)}

## Lineage
| | |
|---|---|
| Model | {model_line} |
| Data | {data_line} |
| Data checks | {quality} |
| Code | {code_line} |
| Runtime | Python {meta["runtime"]["python"]}, scikit-learn {meta["runtime"]["scikit_learn"]} |
{coverage}{perf}{calibration}
## Judge
| Gate | Status | Detail |
|---|---|---|
{gates}

## Limitations
{_bullets(card.limitations)}

## Ethical considerations
{_bullets(card.ethics)}
"""


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(data), 0, 0o644
    tar.addfile(info, io.BytesIO(data))


def build_package(run_dir: Path, out_dir: Path, repo: Path, allow_fail: bool = False) -> Path:
    import yaml

    report = json.loads((run_dir / "report.json").read_text())
    name = report["kind"].removesuffix("_run")
    verdict = report["judge"]["verdict"]
    if verdict not in ("pass", "baseline") and not allow_fail:
        raise ValueError(
            f"run {run_dir.name} has verdict {verdict!r}; refusing to package (use --allow-fail "
            "to build a pipeline-test package that can never be approved)"
        )
    git = git_state(repo)
    model_bytes = (run_dir / "model.joblib").read_bytes()
    spec = yaml.safe_load((PKG_ROOT / f"models/{name}/spec.yaml").read_text())
    meta = {
        "format_version": 1,
        "name": name,
        "version": report["version"],
        "purpose": {"pass": "candidate", "baseline": "baseline"}.get(verdict, "pipeline-test"),
        "kind": spec.get("kind", "scorer"),
        "top_complaints": report.get("top_complaints"),
        "check": report.get("check"),
        "question": spec["question"],
        "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "data": {"pull_id": report["pull_id"], "market_id": report["market_id"], "split": report["split"]},
        "model": {
            "family": report["challenger"]["model"],
            "params": report["challenger"]["params"],
            "features": report["challenger"]["features"],
            "threshold": report["challenger"]["threshold"],
            "sha256": sha256_bytes(model_bytes),
        },
        "champion": report["champion"]["model"],
        "scores": {"challenger": report["challenger"]["test"], "champion": report["champion"]["test"]},
        "judge": report["judge"],
        "code": {"dk_intelligence": __version__, "hashes": code_hashes(name)},
        "runtime": {"python": platform.python_version(), "scikit_learn": sklearn.__version__},
        "git": git,
    }
    files: dict[str, bytes] = {
        "model.joblib": model_bytes,
        "metadata.json": (json.dumps(meta, indent=2, default=str) + "\n").encode(),
        "report.json": (run_dir / "report.json").read_bytes(),
        "model_card.md": model_card(meta, run_dir, run_dir.parent.parent / "quality").encode(),
    }
    for rel in model_sources()[name]:
        files[f"source/{rel}"] = (PKG_ROOT / rel).read_bytes()
    files["SHA256SUMS"] = "".join(f"{sha256_bytes(b)}  {n}\n" for n, b in sorted(files.items())).encode()

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{name}-{meta['version']}"
    tgz = out_dir / f"{stem}.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tar:
        for n in sorted(files):
            _add(tar, f"{stem}/{n}", files[n])
    tgz.write_bytes(buf.getvalue())
    (out_dir / f"{stem}.tar.gz.sha256").write_text(f"{sha256_bytes(buf.getvalue())}  {tgz.name}\n")
    return tgz


def verify_package(tgz: Path) -> list[str]:
    """Problems found (empty = OK): outer checksum, every inner checksum, required files, safe paths."""
    problems = []
    side = tgz.with_name(tgz.name + ".sha256")
    if not side.exists() or side.read_text().split()[0] != sha256_bytes(tgz.read_bytes()):
        problems.append("outer checksum missing or wrong")
    try:
        return problems + _verify_archive(tgz)
    except (tarfile.TarError, EOFError, OSError, zlib.error, UnicodeDecodeError, ValueError) as e:
        return [*problems, f"archive unreadable: {type(e).__name__}"]


def _verify_archive(tgz: Path) -> list[str]:
    problems: list[str] = []
    with tarfile.open(tgz, "r:gz") as tar:
        members = {m.name: m for m in tar.getmembers()}
        if any(
            m.issym() or m.islnk() or ".." in Path(m.name).parts or m.name.startswith("/") for m in members.values()
        ):
            return [*problems, "unsafe paths or links in archive"]
        roots = {Path(n).parts[0] for n in members}
        if len(roots) != 1:
            return [*problems, "archive must contain exactly one top folder"]
        root = roots.pop()

        def read(n: str) -> bytes | None:
            m = members.get(f"{root}/{n}")
            f = tar.extractfile(m) if m else None
            return f.read() if f else None

        sums = read("SHA256SUMS")
        if sums is None:
            return [*problems, "SHA256SUMS missing"]
        listed = {}
        for line in sums.decode().splitlines():
            digest, n = line.split("  ", 1)
            listed[n] = digest
            data = read(n)
            if data is None:
                problems.append(f"missing {n}")
            elif sha256_bytes(data) != digest:
                problems.append(f"checksum mismatch {n}")
        for required in ("model.joblib", "metadata.json", "model_card.md"):
            if required not in listed:
                problems.append(f"{required} not listed")
        extra = {n.removeprefix(f"{root}/") for n, m in members.items() if m.isfile()} - set(listed) - {"SHA256SUMS"}
        if extra:
            problems.append(f"unlisted files: {', '.join(sorted(extra))}")
    return problems


def rsync_command(files: list[Path], target: str, key: Path, dry_run_checksum: bool = False) -> list[str]:
    ssh = f"ssh -i {key} -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=15"
    cmd = ["rsync", "-e", ssh, "--itemize-changes"]  # works with macOS openrsync too (no --chmod there)
    cmd += ["--dry-run", "--checksum"] if dry_run_checksum else ["--checksum"]
    return [*cmd, *map(str, files), f"{target}:"]


def upload(tgz: Path, target: str, key: Path) -> None:
    """Upload the archive first and the .sha256 last (the server treats the .sha256 as 'ready'). Then verify with a
    checksum dry run: with a write-only inbox we cannot read files back, but rsync's receiver compares checksums
    and lists every file that differs; an empty list means the VPS has exactly our bytes."""
    side = tgz.with_name(tgz.name + ".sha256")
    for f in (tgz, side):
        subprocess.run(rsync_command([f], target, key), check=True, capture_output=True, text=True)
    check = subprocess.run(
        rsync_command([tgz, side], target, key, dry_run_checksum=True), check=True, capture_output=True, text=True
    )
    changed = [ln for ln in check.stdout.splitlines() if ln.strip()]
    if changed:
        raise RuntimeError(f"remote copy differs after upload: {changed}")
