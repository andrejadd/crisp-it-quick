"""Stdout logging with secret redaction."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable

from crisp.config import Settings

REDACTED = "***"


class RedactingFilter(logging.Filter):
    """Replaces any known secret value in the final log message."""

    def __init__(self, secrets: Iterable[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        msg = record.getMessage()
        for s in self._secrets:
            msg = msg.replace(s, REDACTED)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            for s in self._secrets:
                record.exc_text = record.exc_text.replace(s, REDACTED)
        record.msg, record.args = msg, None
        return True


def setup_logging(level: str, secrets: Iterable[str] = (), stream=None) -> logging.Handler:
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    handler.addFilter(RedactingFilter(secrets))
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)
    # httpx logs full request lines at INFO; keep it quiet unless debugging.
    logging.getLogger("httpx").setLevel(max(logging.WARNING, root.level))
    return handler


def log_startup_config(settings: Settings, logger: logging.Logger | None = None) -> None:
    logger = logger or logging.getLogger("crisp")
    dump = settings.model_dump(mode="json")
    dump["openrouter_api_key"] = REDACTED if settings.openrouter_api_key else None
    logger.info("starting with configuration: %s", dump)
