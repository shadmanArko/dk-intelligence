"""Tokens for dk_intel over HTTP (ADR 0029, architecture §6.1, §7.3).

Short-lived HS256 JWTs, signed with a key derived from the host's `INTEL_MCP_SECRET` (never the secret itself):
`iss=dk-intelligence`, `aud=dk_intel@<host>`, `sub=<client>`, `scope=<profile>`, `dk_max_sensitivity`, `iat`, `exp`,
`jti`. FastMCP's JWTVerifier checks signature, issuer, audience, expiry and that the token's scope is the profile of
the path it calls; on top of that a revoked `jti` (one per line in the revocation file) is refused. Tokens are written
into `.env` or handed to Claude Code's headers helper; they are never printed to a terminal.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from pathlib import Path
from typing import Any

from fastmcp.server.auth.providers.jwt import JWTVerifier

ISSUER = "dk-intelligence"
ALGORITHM = "HS256"
SENSITIVITY = ("S0", "S1", "S2", "S3")
DEFAULT_CEILING = "S2"  # the owner allowed every client the data on the VPS; review text is S2
MAX_TTL = 90 * 24 * 3600


def audience(host: str = "mac") -> str:
    return f"dk_intel@{host}"


def signing_key(secret: str) -> str:
    """A purpose-bound key: the JWT key, cursor HMACs and audit references never share bytes."""
    if len(secret) < 32:
        raise ValueError("INTEL_MCP_SECRET is too short (run `intel mcp init --write-env`)")
    return hmac.new(secret.encode(), b"dk_intel jwt v1", hashlib.sha256).hexdigest()


def issue(
    secret: str,
    client: str,
    profile: str,
    ttl_s: int,
    host: str = "mac",
    max_sensitivity: str = DEFAULT_CEILING,
    now: float | None = None,
) -> tuple[str, str]:
    """(token, jti)."""
    import jwt

    if not 1 <= ttl_s <= MAX_TTL:
        raise ValueError(f"ttl must be 1 s to {MAX_TTL // 86400} days")
    if max_sensitivity not in SENSITIVITY:
        raise ValueError(f"max_sensitivity must be one of {', '.join(SENSITIVITY)}")
    t = int(now if now is not None else time.time())
    jti = uuid.uuid4().hex
    claims = {
        "iss": ISSUER,
        "aud": audience(host),
        "sub": client,
        "scope": profile,
        "dk_max_sensitivity": max_sensitivity,
        "iat": t,
        "exp": t + ttl_s,
        "jti": jti,
    }
    return jwt.encode(claims, signing_key(secret), algorithm=ALGORITHM), jti


class Revocations:
    """The revocation file, re-read at most every `every_s` seconds (no restart needed to revoke)."""

    def __init__(self, path: Path, every_s: float = 60.0) -> None:
        self.path, self.every_s = path, every_s
        self._jtis: frozenset[str] = frozenset()
        self._read_at = 0.0

    def __contains__(self, jti: object) -> bool:
        now = time.monotonic()
        if now - self._read_at >= self.every_s:
            text = self.path.read_text() if self.path.exists() else ""
            self._jtis = frozenset(line.strip() for line in text.splitlines() if line.strip())
            self._read_at = now
        return jti in self._jtis

    def add(self, jti: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(jti + "\n")
        self._read_at = 0.0


class ProfileVerifier(JWTVerifier):
    """FastMCP's verifier for one profile path, plus the revocation list."""

    def __init__(self, secret: str, profile: str, revoked: Revocations, host: str = "mac") -> None:
        super().__init__(
            public_key=signing_key(secret),
            algorithm=ALGORITHM,
            issuer=ISSUER,
            audience=audience(host),
            required_scopes=[profile],
        )
        self.revoked = revoked

    async def verify_token(self, token: str) -> Any:
        access = await super().verify_token(token)
        if access is None or access.claims.get("jti") in self.revoked:
            return None
        return access


def ceiling_of(claims: dict[str, Any] | None) -> str:
    """The token's sensitivity ceiling; stdio and in-memory clients (no token) get the default."""
    value = (claims or {}).get("dk_max_sensitivity", DEFAULT_CEILING)
    return value if value in SENSITIVITY else "S0"  # an unknown value fails closed
