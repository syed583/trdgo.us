"""Wiring for the live process. Nothing starts unless EARNINGS_MONITOR_ENABLED=1; even then the scheduler does nothing until a valid owner approval file exists (see gate.py)."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config as C
from .adapters import ProdAdapters
from .alerts import FileSink
from .gate import ENV_ENABLED, ProviderAccess, evaluate
from .locks import ScanLock
from .scheduler import Runner, Scheduler
from .service import Adapters, Monitor
from .store import Store

UTC = timezone.utc
ENV_DIR = "EARNINGS_MONITOR_DIR"


def data_dir() -> Path:
    return Path(os.getenv(ENV_DIR) or Path(__file__).resolve().parents[2] / "em_data")


def now() -> datetime:
    return datetime.now(UTC)


def build(dir_: Optional[Path] = None, adapters: Optional[Adapters] = None, clock=now, env: Optional[dict] = None, cfg: Optional[C.Config] = None):
    d = Path(dir_ or data_dir())
    cfg = cfg or C.Config.load(str(d / "config.json"))
    store = Store(d / "monitor.sqlite")
    state0 = evaluate(cfg, d, clock(), env)
    access = ProviderAccess(store, cfg, state0, d, clock, env)
    mon = Monitor(store, cfg, adapters or ProdAdapters(), access, clock, sinks=[FileSink(d / "alerts_outbox.jsonl")])
    sched = Scheduler(store, cfg, mon, access, ScanLock(d / "scan.lock"), lambda t: evaluate(cfg, d, t, env))
    return store, cfg, mon, sched


_runner: Optional[Runner] = None


def start_if_enabled() -> Optional[Runner]:
    """Called once at app start. Returns None (and starts nothing) unless the owner switched the monitor on in the environment."""
    global _runner
    if str(os.getenv(ENV_ENABLED, "")).strip() != "1" or _runner is not None:
        return _runner
    store, cfg, mon, sched = build()
    _runner = Runner(sched, now).start()
    return _runner
