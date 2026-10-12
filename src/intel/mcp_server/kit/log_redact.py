"""Keep tokens out of logs (threat T8): a filter for JWT-shaped strings and Authorization values on every log
record the MCP process emits, and quieter HTTP client loggers (they log request lines, never needed here).
intel-api's own filter matches only Telegram bot tokens, and MCP code may not import intel.serve."""

from __future__ import annotations

import logging
import re

JWT = re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}")
AUTH = re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?bearer\s+)\S+")
REDACTED = "[redacted]"


def redact(text: str) -> str:
    return JWT.sub(REDACTED, AUTH.sub(r"\1" + REDACTED, text))


class RedactTokens(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


def install() -> None:
    """Idempotent: on the root logger and its handlers, plus FastMCP's and uvicorn's loggers."""
    flt = RedactTokens()
    for name in ("", "fastmcp", "uvicorn", "uvicorn.error", "uvicorn.access", "mcp"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactTokens) for f in logger.filters):
            logger.addFilter(flt)
        for handler in logger.handlers:
            if not any(isinstance(f, RedactTokens) for f in handler.filters):
                handler.addFilter(flt)
    for noisy in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
