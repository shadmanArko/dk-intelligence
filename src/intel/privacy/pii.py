"""Personal-data detectors. Precise rather than greedy: a scan that cries wolf on every UUID gets switched off.

- email:    RFC-ish address with a dotted domain;
- phone:    international form only (+49 ..., 0049 ...), 9-15 digits (E.164), not inside a longer token (ids, decimals,
            UUID segments); the business's own numbers are allow-listed by hash in the policy;
- iban:     country code + check digits + BBAN, validated with the ISO 13616 mod-97 checksum;
- identity: a JSON key that holds identity or contact data (`email`, `phone`, `ip`, `from`, ...).

Results are counts per table / column / kind. A value is never returned, logged or stored.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from intel.privacy import policy

EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])")
PHONE = re.compile(r"(?<![\w.\-:/+])(?:\+|00)[1-9][\d \-/()]{7,20}\d(?![\w.\-])")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")


@dataclass(frozen=True)
class Finding:
    table: str
    column: str
    kind: str
    count: int


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _number(s: str) -> str:
    """Country code + subscriber digits: +49 30 1234567 and 0049 30 1234567 are the same number."""
    return _digits(s).removeprefix("00")


def allowed_phone(s: str) -> bool:
    return hashlib.sha256(_number(s).encode()).hexdigest() in set(policy()["pii"]["allow_phone_sha256"])


def is_phone(s: str) -> bool:
    return 9 <= len(_number(s)) <= 15 and not allowed_phone(s)


def _phones(text: str) -> Iterator[re.Match[str]]:
    return (m for m in PHONE.finditer(text) if is_phone(m.group()))


def _iban_ok(candidate: str) -> bool:
    s = candidate.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    return int("".join(str(int(ch, 36)) for ch in moved)) % 97 == 1


def find_in_text(text: str) -> Counter[str]:
    found: Counter[str] = Counter()
    found["email"] += sum(1 for _ in EMAIL.finditer(text))
    found["iban"] += sum(1 for m in IBAN.finditer(text) if _iban_ok(m.group()))
    # account-number-shaped text is not a phone number, whether or not its checksum holds ("... 0044 0532 ...")
    found["phone"] += sum(1 for _ in _phones(IBAN.sub(" ", text)))
    return +found  # drop zero counts


def redact(text: str) -> tuple[str, int]:
    """Replace emails and (non-business) phone numbers; returns the text and the number of replacements."""
    n = 0

    def sub(kind: str) -> Any:
        def f(m: re.Match[str]) -> str:
            nonlocal n
            if kind == "phone" and not is_phone(m.group()):
                return m.group()
            n += 1
            return f"[{kind}]"

        return f

    text = EMAIL.sub(sub("email"), text)
    text = PHONE.sub(sub("phone"), text)
    return text, n


def _walk(obj: Any, identity: set[str], found: Counter[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            # `from` is only an identity when it is an object (Facebook's commenter {id, name}), not a date or a source
            if key in identity and (key != "from" or isinstance(v, dict)) and v not in (None, "", [], {}):
                found[f"identity:{key}"] += 1
            _walk(v, identity, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, identity, found)
    elif isinstance(obj, str):
        found.update(find_in_text(obj))


def scan_value(value: str) -> Counter[str]:
    """A text cell; JSON documents are walked (identity keys + every string leaf)."""
    found: Counter[str] = Counter()
    s = value.lstrip()
    if s[:1] in ("{", "["):
        try:
            _walk(json.loads(value), set(policy()["pii"]["identity_keys"]), found)
            return found
        except ValueError:
            pass
    found.update(find_in_text(value))
    return found


def _strings(col: pa.ChunkedArray) -> Iterable[str]:
    t = col.type
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return (v for v in col.to_pylist() if v)
    if pa.types.is_list(t) and pa.types.is_string(t.value_type):
        return (x for v in col.to_pylist() if v for x in v if x)
    return ()


def scan_table(path: Path, table: str | None = None) -> list[Finding]:
    name = table or path.stem
    t = pq.read_table(path)
    identity = set(policy()["pii"]["identity_keys"])
    out: list[Finding] = []
    for c in t.column_names:
        found: Counter[str] = Counter()
        if c.lower() in identity and t.column(c).null_count < len(t):
            found[f"identity:{c.lower()}"] += len(t) - t.column(c).null_count
        for v in _strings(t.column(c)):
            found.update(scan_value(v))
        out += [Finding(name, c, k, n) for k, n in sorted(found.items())]
    return out


def scan_dir(pull_dir: Path) -> list[Finding]:
    return [f for p in sorted(pull_dir.glob("*.parquet")) for f in scan_table(p)]
