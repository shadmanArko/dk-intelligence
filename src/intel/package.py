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

import sklearn

from intel import __version__

MODEL_SOURCES = {
    "content_scorer": [
        "models/content_scorer/features.py",
        "models/content_scorer/predict.py",
        "models/content_scorer/spec.yaml",
        "champions/harness_v1/features.py",
    ],
    "funnel": ["models/funnel/spec.yaml", "models/funnel/run.py"],
    "occasion_planner": ["models/occasion_planner/spec.yaml", "models/occasion_planner/run.py"],
    "community_map": ["models/community_map/spec.yaml", "models/community_map/run.py"],
    "weakness_miner": [
        "models/weakness_miner/spec.yaml",
        "models/weakness_miner/aspects.yaml",
        "models/weakness_miner/mine.py",
    ],
    "reach_scorer": [
        "models/content_scorer/features.py",
        "models/content_scorer/predict.py",
        "models/reach_scorer/spec.yaml",
        "champions/harness_v1/features.py",
    ],
}
PKG_ROOT = Path(__file__).parent


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def code_hashes(name: str) -> dict[str, str]:
    return {rel: sha256_bytes((PKG_ROOT / rel).read_bytes()) for rel in MODEL_SOURCES[name]}


def git_state(repo: Path) -> dict:
    def run(*a: str) -> str:
        return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=False).stdout.strip()

    return {"commit": run("rev-parse", "HEAD") or None, "dirty": bool(run("status", "--porcelain", "--", "src"))}


def model_card(meta: dict) -> str:
    t, c, j = meta["scores"]["challenger"], meta["scores"]["champion"], meta["judge"]
    gates = "\n".join(f"| {g['gate']} | {g['status']} | {g['detail']} |" for g in j["gates"])
    return f"""# {meta["name"]} {meta["version"]}

**Purpose:** {meta["purpose"]} · **Verdict:** {j["verdict"].upper()} · built {meta["created_at"]}

Question: {meta["question"]}

| | Challenger (this) | Champion |
|---|---|---|
| Test balanced accuracy | {t["balanced_accuracy"]} | {c["balanced_accuracy"]} |
| AUC | {t["roc_auc"]} | {c["roc_auc"]} |
| Brier | {t["brier"]} | {c["brier"]} |
| Test posts | {t["n"]} | {c["n"]} |

Model: {meta["model"]["family"]} on feature set `{meta["model"]["features"]}`, threshold {meta["model"]["threshold"]}.
Data: warehouse pull `{meta["data"]["pull_id"]}`, market snapshot `{meta["data"]["market_id"]}`.
Code: commit `{(meta["git"]["commit"] or "?")[:10]}`{" (uncommitted changes!)" if meta["git"]["dirty"] else ""}.

| Gate | Status | Detail |
|---|---|---|
{gates}
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
        "model_card.md": model_card(meta).encode(),
    }
    for rel in MODEL_SOURCES[name]:
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
