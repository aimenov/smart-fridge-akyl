"""Central logging setup for the Smart Fridge backend."""

from __future__ import annotations

import logging
import logging.config
import sys
from typing import Any


def setup_logging(
    level: str = "INFO",
    *,
    json_logs: bool = False,
    http_trace: bool = True,
) -> None:
    """
    Configure root logging and standard library noise reduction.
    `level`: DEBUG, INFO, WARNING, ERROR.
    """
    lvl = getattr(logging, level.upper(), logging.INFO)
    trace_lvl = logging.INFO if http_trace else logging.WARNING

    if json_logs:
        fmt = '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
        datefmt = None
    else:
        fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {"format": fmt, "datefmt": datefmt},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": sys.stdout,
                    "formatter": "standard",
                    "level": lvl,
                },
            },
            "loggers": {
                # Third-party: avoid duplicate SQL unless debugging
                "sqlalchemy.engine": {"level": "WARNING", "handlers": ["console"], "propagate": False},
                "sqlalchemy.pool": {"level": "WARNING", "handlers": ["console"], "propagate": False},
                "uvicorn": {"level": "INFO", "handlers": ["console"], "propagate": False},
                "uvicorn.access": {"level": "INFO", "handlers": ["console"], "propagate": False},
                "uvicorn.error": {"level": "INFO", "handlers": ["console"], "propagate": False},
                "httpx": {"level": "WARNING", "handlers": ["console"], "propagate": False},
                "httpcore": {"level": "WARNING", "handlers": ["console"], "propagate": False},
                "apscheduler": {"level": "INFO", "handlers": ["console"], "propagate": False},
                # Request middleware (always wired; level follows http_trace / SMART_FRIDGE_HTTP_TRACE)
                "smart_fridge.http": {"level": trace_lvl, "handlers": ["console"], "propagate": False},
            },
            "root": {"level": lvl, "handlers": ["console"]},
        }
    )
