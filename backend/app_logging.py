"""Rotating log files for the parts of the app that must leave a trace when nobody is watching stdout.

``file_logger("tracker")`` -> ``backend/logs/tracker.log`` (5 MB x 5), ``file_logger("uw")`` -> ``logs/uw.log``. Idempotent: a
logger gets its file handler once per process. Never logs a secret: callers log statuses, paths and symbols only. The directory
can be moved with ``TRDGO_LOG_DIR``; a directory that cannot be created leaves the logger on stdout only (nothing fails)."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 5
_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_done: set[str] = set()


def log_dir() -> Path:
    return Path(os.environ.get("TRDGO_LOG_DIR") or (Path(__file__).resolve().parent / "logs"))


def file_logger(name: str, filename: str | None = None, level: int = logging.INFO) -> logging.Logger:
    log = logging.getLogger(name)
    if name in _done or os.environ.get("PYTEST_CURRENT_TEST"):
        return log
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(d / (filename or f"{name}.log"), maxBytes=_MAX_BYTES, backupCount=_BACKUPS,
                                encoding="utf-8")
        h.setFormatter(logging.Formatter(_FMT))
        h.setLevel(level)
        log.addHandler(h)
        if log.level == logging.NOTSET or log.level > level:
            log.setLevel(level)
    except Exception:  # noqa: BLE001 - logging must never take the app down
        pass
    _done.add(name)
    return log
