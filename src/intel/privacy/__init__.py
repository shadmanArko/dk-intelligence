"""Data protection and responsible AI (ADR 0024). `policy.yaml` is the single source; this package enforces it:

- `pii`        detectors (email, phone, IBAN, identity keys in JSON) and the scan of a pull; counts, never values
- `guard`      what a pull may contain: declared tables only, redaction of third-party free text, a failing scan
- `fairness`   the package gate: no protected-attribute fields (except reviewed exceptions), no blocked words, no PII
- `retention`  the daily deletion job in intel-api (audited), from the policy's retention rules
- `ropa`       the record of processing (GDPR Art. 30 style), rendered from the policy
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

POLICY_FILE = Path(__file__).with_name("policy.yaml")


class PrivacyViolation(RuntimeError):
    """Data that the policy does not allow; the operation (a pull, a package) is refused."""


@cache
def policy() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(POLICY_FILE.read_text())
    return data


def tables(section: str) -> dict[str, dict[str, Any]]:
    """The declared tables of `warehouse_pull`, `market_pull` or `intel_db`."""
    out: dict[str, dict[str, Any]] = policy()[section]["tables"]
    return out


def pull_tables() -> list[str]:
    """The warehouse tables a pull copies (purpose limitation): everything else stays on the VPS."""
    return sorted(tables("warehouse_pull"))
