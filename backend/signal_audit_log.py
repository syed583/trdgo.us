"""
Audit log for every intraday signal decision -- taken OR blocked.

For each name the board resolves, one row is recorded with: the time (ET and
Dubai), the stock, both scores, where SPY/QQQ sit vs their VWAP, which gates
passed/failed, the resulting state, and (if blocked) why. The log can be
exported to CSV.
"""

from __future__ import annotations

import csv
import io
import json
import threading
from pathlib import Path
from typing import Optional

_FILE = Path(__file__).with_name(".sec_cache") / "signal_audit.json"
_CAP = 5000
_lock = threading.Lock()
_log: list[dict] = []
_loaded = False

FIELDS = [
    "time_et", "time_dubai", "horizon", "symbol", "state", "side", "size",
    "buy_score", "sell_score", "spy_vs_vwap", "qqq_vs_vwap",
    "price_vs_vwap", "rvol", "buy_gates_pass", "sell_gates_pass",
    "gate_fails", "blocked_reason",
]


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if _FILE.exists():
            _log.extend(json.loads(_FILE.read_text("utf-8")))
    except Exception:  # noqa: BLE001
        pass


def _save() -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(_log[-_CAP:]), "utf-8")
    except Exception:  # noqa: BLE001
        pass


def record(rows: list[dict]) -> None:
    """Append a batch of audit rows (one per name evaluated this pass)."""
    if not rows:
        return
    _load()
    with _lock:
        _log.extend(rows)
        del _log[:-_CAP]
        _save()


def recent(horizon: Optional[str] = None, limit: int = 500) -> list[dict]:
    _load()
    with _lock:
        rows = [r for r in _log if (not horizon or r.get("horizon") == horizon)]
    return list(reversed(rows))[:max(1, min(limit, _CAP))]


def to_csv(horizon: Optional[str] = None) -> str:
    rows = recent(horizon, _CAP)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        row = dict(r)
        gf = row.get("gate_fails")
        if isinstance(gf, (list, tuple)):
            row["gate_fails"] = "; ".join(gf)
        w.writerow(row)
    return buf.getvalue()
