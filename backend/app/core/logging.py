"""Structured JSON logging with request/job context binding."""
from __future__ import annotations

import logging
import sys

import structlog

_configured = False


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    global _configured
    if _configured:
        return
    logging.basicConfig(format="%(message)s", stream=sys.stdout,
                        level=getattr(logging, level.upper(), logging.INFO))
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(structlog.processors.JSONRenderer() if json_output
                      else structlog.dev.ConsoleRenderer(colors=True))
    structlog.configure(processors=processors,
                        logger_factory=structlog.stdlib.LoggerFactory(),
                        cache_logger_on_first_use=True)
    _configured = True


def get_logger(name: str | None = None):
    configure_logging()
    return structlog.get_logger(name)


def bind(**kwargs) -> None:
    structlog.contextvars.bind_contextvars(**kwargs)


def clear() -> None:
    structlog.contextvars.clear_contextvars()
