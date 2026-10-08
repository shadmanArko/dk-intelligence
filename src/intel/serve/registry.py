"""Model registry: inbox intake, approval decisions, activation, rollback, verified loading.

Safety rules enforced here (not in the UI):
- A package is only extracted after `verify_package` passes, and only the files listed in its SHA256SUMS are
  written (no paths from the archive are trusted).
- Only `candidate` packages with verdict `pass`, or `baseline` packages, can be offered for approval.
- A package whose feature-code hashes differ from the code this server runs is `incompatible` (training and
  serving would compute different features).
- `model.joblib` is hash-checked against the metadata before `joblib.load` (pickle executes code).
- Every decision is one row; activations are append-only; rollback re-activates the previous package.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path

import joblib

from intel.package import code_hashes, sha256_bytes, verify_package
from intel.serve.db import connect, log_event, one
from intel.serve.lifecycle import audit

APPROVABLE = {("candidate", "pass"), ("baseline", "baseline")}
REASON_CODES = {
    "not_better": "Not convincing enough",
    "wrong_timing": "Not now (busy week / campaign running)",
    "suspicious": "Something looks wrong",
    "other": "Other",
}


class RegistryError(RuntimeError):
    pass


@dataclass
class Intake:
    package_id: int
    status: str
    name: str
    version: str
    problems: list[str]
    approval_id: int | None


def _read_member(tar: tarfile.TarFile, path: str) -> bytes:
    f = tar.extractfile(path)
    if f is None:
        raise RegistryError(f"{path} missing")
    return f.read()


class Registry:
    def __init__(self, dsn: str, inbox: Path, store: Path, approval_days: int = 7):
        self.dsn, self.inbox, self.store, self.approval_days = dsn, inbox, store, approval_days

    # ---------- intake ----------
    def scan_inbox(self) -> list[Intake]:
        out = []
        for side in sorted(self.inbox.glob("*.tar.gz.sha256")):
            tgz = side.with_name(side.name.removesuffix(".sha256"))
            if not tgz.exists():
                continue
            try:
                outer = side.read_text().split()[0]
            except (OSError, IndexError):
                continue
            with connect(self.dsn) as c:
                if c.execute("select 1 from model_package where sha256 = %s", (outer,)).fetchone():
                    continue
            out.append(self._intake(tgz, outer))
        return out

    def _intake(self, tgz: Path, outer: str) -> Intake:
        problems = verify_package(tgz)
        meta: dict = {}
        files: dict[str, bytes] = {}
        if not problems:
            with tarfile.open(tgz, "r:gz") as tar:
                root = tar.getmembers()[0].name.split("/")[0]
                sums = _read_member(tar, f"{root}/SHA256SUMS").decode()
                for line in sums.splitlines():
                    digest, rel = line.split("  ", 1)
                    data = _read_member(tar, f"{root}/{rel}")
                    if sha256_bytes(data) != digest:
                        problems.append(f"checksum mismatch {rel}")
                    files[rel] = data
                files["SHA256SUMS"] = sums.encode()
            meta = json.loads(files["metadata.json"])
        name = meta.get("name") or tgz.name.split("-cs")[0]
        version = meta.get("version") or tgz.name
        purpose, verdict = meta.get("purpose", "unknown"), meta.get("judge", {}).get("verdict", "unknown")

        if problems:
            status = "invalid"
        elif (purpose, verdict) not in APPROVABLE:
            status = "test_only" if purpose == "pipeline-test" else "invalid"
            if status == "invalid":
                problems.append(f"purpose {purpose!r} with verdict {verdict!r} cannot be approved")
        else:
            try:
                ours = code_hashes(name)
            except KeyError:
                ours = None
            theirs = meta.get("code", {}).get("hashes")
            if ours != theirs:
                status = "incompatible"
                diff = sorted(
                    k for k in set(ours or {}) | set(theirs or {}) if (ours or {}).get(k) != (theirs or {}).get(k)
                )
                problems.append("feature code differs from the server's: " + ", ".join(diff))
            else:
                status = "pending_approval"

        store_path = None
        if status == "pending_approval":
            dest = self.store / name / version
            dest.mkdir(parents=True, exist_ok=True)
            for rel, data in files.items():  # only checksummed, listed files; no archive paths
                p = (dest / rel).resolve()
                if not p.is_relative_to(dest.resolve()):
                    raise RegistryError(f"refusing path {rel}")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            store_path = str(dest)

        with connect(self.dsn) as c:
            row = one(
                c.execute(
                    """insert into model_package (name, version, file_name, sha256, purpose, verdict, status, problems,
                                              metadata, store_path)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   on conflict (name, version) do update set problems = model_package.problems || excluded.problems
                   returning id, (xmax = 0) as inserted""",
                    (name, version, tgz.name, outer, purpose, verdict, status, problems, json.dumps(meta), store_path),
                )
            )
            if not row["inserted"]:  # same version uploaded again with different bytes
                log_event(self.dsn, "duplicate_version", name=name, version=version, sha256=outer)
                return Intake(row["id"], "invalid", name, version, ["version already registered"], None)
            approval_id = None
            subject = f"{name} {version}"
            audit(
                c,
                "system",
                "inbox",
                "package_received",
                subject,
                status=status,
                purpose=purpose,
                verdict=verdict,
                sha256=outer,
                problems=problems,
            )
            if status == "pending_approval":
                approval_id = one(
                    c.execute(
                        "insert into approval (package_id, expires_at) values (%s, now() + %s) returning id",
                        (row["id"], dt.timedelta(days=self.approval_days)),
                    )
                )["id"]
                audit(
                    c,
                    "system",
                    "inbox",
                    "approval_requested",
                    subject,
                    approval_id=approval_id,
                    expires_in_days=self.approval_days,
                )
        log_event(self.dsn, "package_received", name=name, version=version, status=status, problems=problems)
        if approval_id and meta.get("kind") == "insights" and self.human_approved_before(name, row["id"]):
            # Insights refresh weekly (new dates, new reviews). After the owner approved this insight once, a new
            # version that passed every gate is applied automatically; Telegram says so, rollback stays one tap.
            self.approve(approval_id, "auto-refresh", "system")
            return Intake(row["id"], "auto_approved", name, version, problems, approval_id)
        return Intake(row["id"], status, name, version, problems, approval_id)

    def human_approved_before(self, name: str, exclude_package: int) -> bool:
        with connect(self.dsn) as c:
            return (
                c.execute(
                    """select 1 from approval a join model_package p on p.id = a.package_id
                   where p.name = %s and p.id <> %s and a.decision = 'approved' and a.channel <> 'system'
                   limit 1""",
                    (name, exclude_package),
                ).fetchone()
                is not None
            )

    # ---------- decisions ----------
    def _open_approval(self, c, approval_id: int) -> dict:
        a = c.execute(
            """select a.*, p.name, p.version, p.status as package_status from approval a
               join model_package p on p.id = a.package_id where a.id = %s for update of a""",
            (approval_id,),
        ).fetchone()
        if a is None:
            raise RegistryError(f"approval {approval_id} not found")
        if a["decision"] is not None:
            raise RegistryError(f"already {a['decision']} ({a['decided_by']}, {a['decided_at']:%Y-%m-%d %H:%M})")
        if a["expires_at"] < dt.datetime.now(dt.UTC):
            raise RegistryError("this approval has expired")
        return a

    def approve(self, approval_id: int, by: str, channel: str) -> dict:
        with connect(self.dsn) as c, c.transaction():
            a = self._open_approval(c, approval_id)
            c.execute(
                "update approval set decision='approved', decided_at=now(), decided_by=%s, channel=%s where id=%s",
                (by, channel, approval_id),
            )
            c.execute("update model_package set status='approved' where id=%s", (a["package_id"],))
            c.execute(
                "insert into model_activation (name, package_id, activated_by, reason) values (%s,%s,%s,'approval')",
                (a["name"], a["package_id"], f"{channel}:{by}"),
            )
            subject = f"{a['name']} {a['version']}"
            audit(c, by, channel, "approved", subject, approval_id=approval_id)
            audit(c, by, channel, "activated", subject, reason="approval")
        log_event(
            self.dsn, "approved", approval_id=approval_id, name=a["name"], version=a["version"], by=by, channel=channel
        )
        return {"name": a["name"], "version": a["version"]}

    def reject(self, approval_id: int, by: str, channel: str, reason_code: str) -> dict:
        if reason_code not in REASON_CODES:
            raise RegistryError(f"unknown reason {reason_code!r}")
        with connect(self.dsn) as c, c.transaction():
            a = self._open_approval(c, approval_id)
            c.execute(
                "update approval set decision='rejected', decided_at=now(), decided_by=%s, channel=%s, "
                "reason_code=%s where id=%s",
                (by, channel, reason_code, approval_id),
            )
            c.execute("update model_package set status='rejected' where id=%s", (a["package_id"],))
            audit(
                c, by, channel, "rejected", f"{a['name']} {a['version']}", approval_id=approval_id, reason=reason_code
            )
        log_event(
            self.dsn,
            "rejected",
            approval_id=approval_id,
            name=a["name"],
            version=a["version"],
            by=by,
            reason=reason_code,
        )
        return {"name": a["name"], "version": a["version"]}

    def expire_due(self) -> list[dict]:
        with connect(self.dsn) as c, c.transaction():
            rows = c.execute(
                """update approval a set decision='expired', decided_at=now(), decided_by='system', channel='system'
                   from model_package p where p.id = a.package_id and a.decision is null and a.expires_at < now()
                   returning a.id, a.package_id, a.telegram_message_id, p.name, p.version"""
            ).fetchall()
            for r in rows:
                c.execute("update model_package set status='expired' where id=%s", (r["package_id"],))
                audit(c, "system", "system", "expired", f"{r['name']} {r['version']}", approval_id=r["id"])
        return rows

    def rollback(self, name: str, by: str, channel: str) -> dict:
        with connect(self.dsn) as c, c.transaction():
            c.execute("select pg_advisory_xact_lock(hashtext(%s))", (name,))
            hist = c.execute(
                "select package_id from model_activation where name=%s order by id desc", (name,)
            ).fetchall()
            if not hist:
                raise RegistryError(f"no active {name} model")
            current = hist[0]["package_id"]
            previous = next((h["package_id"] for h in hist[1:] if h["package_id"] != current), None)
            if previous is None:
                raise RegistryError("nothing to roll back to: this is the first approved model")
            c.execute(
                "insert into model_activation (name, package_id, activated_by, reason) values (%s,%s,%s,'rollback')",
                (name, previous, f"{channel}:{by}"),
            )
            v = one(c.execute("select version from model_package where id=%s", (previous,)))["version"]
            audit(c, by, channel, "rolled_back", f"{name} {v}", from_package=current, to_package=previous)
        log_event(self.dsn, "rollback", name=name, to_version=v, by=by, channel=channel)
        return {"name": name, "version": v}

    def set_telegram_message(self, approval_id: int, message_id: int) -> None:
        with connect(self.dsn) as c:
            c.execute("update approval set telegram_message_id=%s where id=%s", (message_id, approval_id))

    # ---------- reads ----------
    def active(self) -> dict[str, dict]:
        with connect(self.dsn) as c:
            rows = c.execute(
                """select distinct on (a.name) a.name, a.activated_at, a.activated_by, a.reason, p.*
                   from model_activation a join model_package p on p.id = a.package_id
                   order by a.name, a.id desc"""
            ).fetchall()
        return {r["name"]: r for r in rows}

    def pending(self) -> list[dict]:
        with connect(self.dsn) as c:
            return c.execute(
                """select a.id as approval_id, a.expires_at, a.requested_at, p.* from approval a
                   join model_package p on p.id = a.package_id
                   where a.decision is null and a.expires_at >= now() order by a.id"""
            ).fetchall()

    def packages(self, limit: int = 50) -> list[dict]:
        with connect(self.dsn) as c:
            return c.execute(
                """select p.*, a.id as approval_id, a.decision, a.decided_by, a.decided_at, a.reason_code
                   from model_package p left join lateral (
                     select * from approval where package_id = p.id order by id desc limit 1) a on true
                   order by p.id desc limit %s""",
                (limit,),
            ).fetchall()

    def package(self, package_id: int) -> dict | None:
        with connect(self.dsn) as c:
            return c.execute("select * from model_package where id=%s", (package_id,)).fetchone()

    def activations(self, limit: int = 20) -> list[dict]:
        with connect(self.dsn) as c:
            return c.execute(
                """select a.*, p.version from model_activation a join model_package p on p.id = a.package_id
                   order by a.id desc limit %s""",
                (limit,),
            ).fetchall()

    def approval(self, approval_id: int) -> dict | None:
        with connect(self.dsn) as c:
            return c.execute(
                "select a.*, p.name, p.version, p.metadata from approval a join model_package p on p.id=a.package_id "
                "where a.id=%s",
                (approval_id,),
            ).fetchone()


def load_bundle(package: dict) -> dict:
    """Hash-check model.joblib against the package metadata, then load."""
    path = Path(package["store_path"]) / "model.joblib"
    expected = package["metadata"]["model"]["sha256"]
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise RegistryError(f"model.joblib hash mismatch for {package['version']}; refusing to load")
    return joblib.load(path)
