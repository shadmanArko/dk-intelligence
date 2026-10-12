"""Knowledge packages: how packs and serving models travel to the VPS through the upload-only inbox (door 2; ADR 0030).

Three kinds, each a `knowledge-<kind>-<id>.tar.gz` with a `.sha256` sidecar (the same convention as model packages):
- `pack`: one gated knowledge pack (manifest, brand, index, tables) plus the Mac's `gate.json`;
- `models`: the ONNX embedder and reranker files (sent once, again only when they change);
- `activate`: a tiny marker naming an already-delivered pack to make active again (rollback; the bytes are there).

Inside: one root folder `<kind>-<id>/` with `knowledge.json` and `SHA256SUMS` listing every other file. The VPS side
(`intake`, run by intel-api's inbox worker) verifies the outer checksum, every inner checksum, refuses any path that
leaves its folder, and for a pack also re-verifies the pack's own manifest and requires `gate.json` to say passed.
Only then does it move the files into the knowledge volume and update `packs/ACTIVE`. Large files are streamed: the
worker runs in intel-api's 512 MB.

Standard library only: intel-api's image has neither pandas nor onnxruntime.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PREFIX = "knowledge-"
KINDS = ("pack", "models", "activate")
CHUNK = 1 << 20
KEEP_PACKS = 2  # the active pack and the one before it (rollback)


class ShipError(RuntimeError):
    pass


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(CHUNK):
            h.update(block)
    return h.hexdigest()


def _files(root: Path) -> Iterator[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            yield p


# ---------- Mac side: build ----------


def build(kind: str, ident: str, sources: dict[str, Path], meta: dict[str, Any], out_dir: Path) -> Path:
    """`sources` maps a folder name inside the package to a local folder (or file). Returns the .tar.gz path."""
    if kind not in KINDS:
        raise ShipError(f"unknown kind {kind!r}")
    root = f"{kind}-{ident}"
    out_dir.mkdir(parents=True, exist_ok=True)
    tgz = out_dir / f"{PREFIX}{root}.tar.gz"
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        stage = Path(tmp) / root
        stage.mkdir()
        (stage / "knowledge.json").write_text(json.dumps({"kind": kind, "id": ident, **meta}, sort_keys=True))
        for name, src in sources.items():
            if src.is_dir():
                shutil.copytree(src, stage / name, symlinks=False)
            else:
                shutil.copyfile(src, stage / name)
        sums = [f"{_sha_file(p)}  {p.relative_to(stage).as_posix()}" for p in _files(stage)]
        (stage / "SHA256SUMS").write_text("\n".join(sums) + "\n")
        with tarfile.open(tgz, "w:gz") as tar:
            tar.add(stage, arcname=root)
    tgz.with_name(tgz.name + ".sha256").write_text(f"{_sha_file(tgz)}  {tgz.name}\n")
    return tgz


def pack_package(pack_root: Path, pack_id: str, out_dir: Path) -> Path:
    gate = pack_root / "gates" / f"{pack_id}.json"
    if not gate.exists() or not json.loads(gate.read_text()).get("passed"):
        raise ShipError(f"{pack_id} has no passed gate result: only gated packs are shipped")
    src = pack_root / pack_id
    return build("pack", pack_id, {"pack": src, "gate.json": gate}, {}, out_dir)


def models_package(model_root: Path, repos: list[str], out_dir: Path) -> Path:
    folders = {f"models/{r.replace('/', '--')}": model_root / r.replace("/", "--") for r in repos}
    missing = [str(p) for p in folders.values() if not p.is_dir()]
    if missing:
        raise ShipError("model folders missing: " + ", ".join(missing))
    digest = hashlib.sha256("".join(sorted(_sha_file(f) for p in folders.values() for f in _files(p))).encode())
    return build("models", digest.hexdigest()[:12], folders, {"repos": repos}, out_dir)


def activate_package(pack_id: str, out_dir: Path) -> Path:
    return build("activate", pack_id, {}, {"pack_id": pack_id}, out_dir)


# ---------- VPS side: intake ----------


@dataclass
class Intake:
    file: str
    kind: str
    ident: str
    status: str  # installed | activated | invalid
    problems: list[str]


def _safe_members(tar: tarfile.TarFile, root: str) -> list[tarfile.TarInfo]:
    out = []
    for m in tar.getmembers():
        parts = Path(m.name).parts
        if not parts or parts[0] != root or ".." in parts or Path(m.name).is_absolute():
            raise ShipError(f"refusing path {m.name!r}")
        if not (m.isfile() or m.isdir()):
            raise ShipError(f"refusing non-regular member {m.name!r}")
        out.append(m)
    return out


def _extract_verified(tgz: Path, work: Path) -> tuple[dict[str, Any], Path]:
    """Extract into `work` (streamed), then check every file against SHA256SUMS. Returns (knowledge.json, root dir)."""
    with tarfile.open(tgz, "r:gz") as tar:
        first = tar.next()
        if first is None:
            raise ShipError("empty package")
        root = Path(first.name).parts[0]
        for m in _safe_members(tar, root):
            dest = work / m.name
            if m.isdir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            if src is None:
                raise ShipError(f"{m.name} unreadable")
            with dest.open("wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
    base = work / root
    listed: dict[str, str] = {}
    for line in (base / "SHA256SUMS").read_text().splitlines():
        digest, rel = line.split("  ", 1)
        listed[rel] = digest
    present = {p.relative_to(base).as_posix() for p in _files(base)} - {"SHA256SUMS"}
    if present != set(listed):
        raise ShipError(f"files not listed in SHA256SUMS: {sorted(present ^ set(listed))[:5]}")
    for rel, digest in listed.items():
        if _sha_file(base / rel) != digest:
            raise ShipError(f"checksum mismatch {rel}")
    meta: dict[str, Any] = json.loads((base / "knowledge.json").read_text())
    if meta.get("kind") not in KINDS or f"{meta['kind']}-{meta.get('id')}" != root:
        raise ShipError("knowledge.json does not match the package")
    return meta, base


def _set_active(packs: Path, pack_id: str) -> None:
    tmp = packs / ".ACTIVE.tmp"
    tmp.write_text(pack_id + "\n")
    os.replace(tmp, packs / "ACTIVE")


def _prune(packs: Path) -> None:
    """Keep the active pack and the newest other one; older packs are rebuildable copies."""
    active = (packs / "ACTIVE").read_text().strip() if (packs / "ACTIVE").exists() else None
    others = sorted((p for p in packs.glob("kp-*") if p.is_dir() and p.name != active), reverse=True)
    for old in others[KEEP_PACKS - 1 :]:
        shutil.rmtree(old, ignore_errors=True)


def intake(tgz: Path, knowledge: Path) -> Intake:
    """Verify and install one knowledge package into the knowledge volume."""
    from intel.rag.pack import PackError, verify

    kind, ident = "?", "?"
    side = tgz.with_name(tgz.name + ".sha256")
    knowledge.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".incoming-", dir=knowledge))
    try:
        if _sha_file(tgz) != side.read_text().split()[0]:
            raise ShipError("outer checksum mismatch")
        meta, base = _extract_verified(tgz, work)
        kind, ident = str(meta["kind"]), str(meta["id"])
        packs = knowledge / "packs"
        packs.mkdir(exist_ok=True)
        if kind == "models":
            models = knowledge / "models"
            models.mkdir(exist_ok=True)
            for folder in sorted((base / "models").iterdir()):
                dest = models / folder.name
                if dest.exists():
                    shutil.rmtree(dest)
                os.replace(folder, dest)
            return Intake(tgz.name, kind, ident, "installed", [])
        if kind == "activate":
            pack_id = str(meta["pack_id"])
            verify(packs / pack_id)  # the bytes must already be here and intact
            _set_active(packs, pack_id)
            return Intake(tgz.name, kind, ident, "activated", [])
        gate = json.loads((base / "gate.json").read_text())
        if not gate.get("passed") or gate.get("candidate") != ident:
            raise ShipError("the pack's gate did not pass")
        verify(base / "pack")  # the pack's own manifest checksums, independent of the package's
        dest = packs / ident
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(base / "pack", dest)
        _set_active(packs, ident)
        _prune(packs)
        return Intake(tgz.name, kind, ident, "activated", [])
    except (ShipError, PackError, OSError, KeyError, ValueError, tarfile.TarError) as e:
        return Intake(tgz.name, kind, ident, "invalid", [str(e)[:300]])
    finally:
        shutil.rmtree(work, ignore_errors=True)
