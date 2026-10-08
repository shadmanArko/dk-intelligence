"""The sealed-code lock (ADR 0011): `sealed-code.lock.json` pins the hash of every file sealed into a model package.

Packages carry these hashes and the VPS refuses a package whose hashes differ from the code it runs, so changing a
sealed file is a MODEL change, never a cosmetic one. A test compares the lock with the code; after a deliberate model
change run `uv run python -m intel.sealed --update` and commit the lock with the change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from intel.package import code_hashes
from intel.plugins import model_sources

LOCK = Path(__file__).resolve().parents[2] / "sealed-code.lock.json"


def current() -> dict[str, dict[str, str]]:
    return {name: code_hashes(name) for name in sorted(model_sources())}


def diff(lock: Path | None = None) -> list[str]:
    """Human-readable differences between the lock and the code (empty = in sync)."""
    lock = lock or LOCK
    pinned: dict[str, dict[str, str]] = json.loads(lock.read_text()) if lock.exists() else {}
    now, out = current(), []
    for name in sorted(set(pinned) | set(now)):
        a, b = pinned.get(name, {}), now.get(name, {})
        if name not in pinned:
            out.append(f"{name}: new model (not in the lock)")
        elif name not in now:
            out.append(f"{name}: in the lock but no plug-in produces it any more")
        else:
            out += [f"{name}: {f} changed" for f in sorted(set(a) | set(b)) if a.get(f) != b.get(f)]
    return out


def main(argv: list[str]) -> int:
    if "--update" in argv:
        LOCK.write_text(json.dumps(current(), indent=2, sort_keys=True) + "\n")
        print(f"wrote {LOCK.name}")
        return 0
    problems = diff()
    print("\n".join(problems) or "sealed code matches the lock")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
