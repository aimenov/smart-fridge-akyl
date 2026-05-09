"""Central logging: verbose rotating file + quiet console + short scan summary lines."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.app.config import settings

SUMMARY_LOGGER_NAME = "smart_fridge.summary"
EXPIRY_LOGGER_NAME = "smart_fridge.expiry"
RECOGNITION_LOGGER_NAME = "smart_fridge.recognition"


def get_summary_logger() -> logging.Logger:
    """One-line scan outcomes (barcode + product guess); mirrored to the log file."""
    return logging.getLogger(SUMMARY_LOGGER_NAME)

def get_expiry_logger() -> logging.Logger:
    """Expiry OCR milestones; printed on console and mirrored to the log file."""
    return logging.getLogger(EXPIRY_LOGGER_NAME)


def get_recognition_logger() -> logging.Logger:
    """Barcode/OCR pipeline milestones (decode path, consensus); console + file."""
    return logging.getLogger(RECOGNITION_LOGGER_NAME)


def setup_logging(level: str = "INFO", *, json_logs: bool = False) -> None:
    """
    - **File** (see ``settings.log_file``): full timestamps and logger names (trace-style detail stays here).
    - **Console**: WARNING and above only — avoids trace-id noise.
    - **Summary** logger: INFO, plain ``%(message)s`` on stderr + same lines in the log file.
    - **Recognition** logger (``smart_fridge.recognition``): INFO barcode/expiry pipeline milestones on stderr + file.
    """
    lvl = getattr(logging, level.upper(), logging.INFO)
    console_lvl = getattr(
        logging, settings.console_log_level.upper(), logging.WARNING
    )

    if json_logs:
        fmt_file = '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
        datefmt = None
    else:
        fmt_file = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"

    log_path = Path(settings.log_file).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)

    file_handler = RotatingFileHandler(
        str(log_path),
        maxBytes=max(100_000, settings.log_file_max_bytes),
        backupCount=max(1, settings.log_file_backup_count),
        encoding="utf-8",
    )
    file_lvl = getattr(logging, settings.file_log_level.upper(), logging.DEBUG)
    file_handler.setLevel(file_lvl)
    file_handler.setFormatter(logging.Formatter(fmt_file, datefmt=datefmt))

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(console_lvl)
    console_handler.setFormatter(
        logging.Formatter("%(levelname)s | %(name)s | %(message)s")
    )

    # Root DEBUG so ``logger.debug`` from app code reaches the file; console handler still filters.
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    for name in (
        "sqlalchemy.engine",
        "sqlalchemy.pool",
        "uvicorn",
        "uvicorn.access",
        "httpx",
        "httpcore",
        "apscheduler",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)

    sum_log = logging.getLogger(SUMMARY_LOGGER_NAME)
    for h in sum_log.handlers[:]:
        sum_log.removeHandler(h)
    sum_log.setLevel(logging.INFO)
    sum_log.propagate = False
    sum_brief = logging.StreamHandler(sys.stderr)
    sum_brief.setLevel(logging.INFO)
    sum_brief.setFormatter(logging.Formatter("%(message)s"))
    sum_log.addHandler(sum_brief)
    sum_log.addHandler(file_handler)

    exp_log = logging.getLogger(EXPIRY_LOGGER_NAME)
    for h in exp_log.handlers[:]:
        exp_log.removeHandler(h)
    exp_log.setLevel(logging.INFO)
    exp_log.propagate = False
    exp_brief = logging.StreamHandler(sys.stderr)
    exp_brief.setLevel(logging.INFO)
    exp_brief.setFormatter(logging.Formatter("%(message)s"))
    exp_log.addHandler(exp_brief)
    exp_log.addHandler(file_handler)

    rec_log = logging.getLogger(RECOGNITION_LOGGER_NAME)
    for h in rec_log.handlers[:]:
        rec_log.removeHandler(h)
    rec_log.setLevel(logging.INFO)
    rec_log.propagate = False
    rec_brief = logging.StreamHandler(sys.stderr)
    rec_brief.setLevel(logging.INFO)
    rec_brief.setFormatter(logging.Formatter("%(message)s"))
    rec_log.addHandler(rec_brief)
    rec_log.addHandler(file_handler)
