"""Point a copied MLflow tracking database at its new artifact folder (ADR 0044, run once when the data moves).

MLflow stores absolute artifact paths (experiments.artifact_location, runs.artifact_uri, ...). The Mac's database
says /Users/.../dk-intelligence/mlruns; on the server the artifacts live in /data/mlruns. This rewrites every text
value that starts with the old prefix, in one transaction, and prints how many values changed per column.

    python rebase_mlflow.py /data/mlflow.db "/Users/<you>/.../dk-intelligence/mlruns" /data/mlruns
"""

from __future__ import annotations

import sqlite3
import sys


def rebase(db: str, old: str, new: str) -> dict[str, int]:
    old, new = old.rstrip("/"), new.rstrip("/")
    changed: dict[str, int] = {}
    con = sqlite3.connect(db)
    try:
        with con:
            tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
            for t in tables:
                for col in con.execute(f'pragma table_info("{t}")').fetchall():
                    name, typ = col[1], (col[2] or "").upper()
                    if not any(k in typ for k in ("CHAR", "TEXT", "CLOB")) and typ:
                        continue
                    n = con.execute(
                        f'update "{t}" set "{name}" = ? || substr("{name}", ?) '
                        f'where typeof("{name}") = \'text\' and ("{name}" = ? or "{name}" like ? escape \'\\\')',
                        (new, len(old) + 1, old, old.replace("%", r"\%").replace("_", r"\_") + "/%"),
                    ).rowcount
                    if n:
                        changed[f"{t}.{name}"] = n
    finally:
        con.close()
    return changed


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    got = rebase(*sys.argv[1:])
    for k, n in sorted(got.items()):
        print(f"{k}: {n}")
    print(f"total: {sum(got.values())}")
