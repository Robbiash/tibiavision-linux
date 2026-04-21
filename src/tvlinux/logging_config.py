"""Structured logging setup using structlog.

We keep the dependency tree thin: structlog only, with a stdlib fallback when it is not
installed (e.g. minimal testing environments).
"""

from __future__ import annotations

import logging
import sys
from typing import Any


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging and structlog processors."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=numeric_level,
    )

    try:
        import structlog
    except ImportError:  # pragma: no cover - optional at runtime
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """Return a structlog bound logger, falling back to stdlib logging."""
    try:
        import structlog

        return structlog.get_logger(name)
    except ImportError:  # pragma: no cover
        return logging.getLogger(name)
