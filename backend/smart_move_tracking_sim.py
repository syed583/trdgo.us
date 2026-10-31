"""SIMULATED prediction history and outcomes for demos and tests of the tracking layer. Everything here is labelled mode=SIMULATION. No provider, no request, no real symbol data.

Outcomes are drawn independently of the signals on purpose: the simulated model has NO skill, so a correct report must show roughly chance-level hit rates and no claim.
"""
import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import smart_move_tracking as T

UTC = timezone.utc


def _signal(rng, bull, bear):
    return "BUY" if bull >= 14 and bear <= 4 and bull - bear >= 10 else "SELL" if bear >= 14 and bull <= 4 and bear - bull >= 10 else "NO_TRADE"


def simulate(store_dir, track_dir, *, weeks=10, per_week=14, seed=7, start=datetime(2026, 8, 3, tzinfo=UTC)):
    """Writes predictions.jsonl (live-store format) into store_dir and outcomes.jsonl into track_dir. Returns counts."""
    rng = random.Random(seed)
    store, track = Path(store_dir), Path(track_dir)
    store.mkdir(parents=True, exist_ok=True)
    preds, outs = [], []
    for w in range(weeks):
        for i in range(per_week):
            sym = "SIM%02d%02d" % (w, i)
            ed = (start + timedelta(weeks=w, days=rng.randrange(0, 5))).date()
            late = rng.random() < 0.05
            cap = datetime(ed.year, ed.month, ed.day, 4, 0, tzinfo=UTC) - timedelta(hours=rng.randrange(26, 46)) if not late else datetime(ed.year, ed.month, ed.day, 15, 0, tzinfo=UTC)
            versions = 2 if rng.random() < 0.15 else 1
            for ver in range(1, versions + 1):
                eng = {}
                for var in T.VARIANTS:
                    bull, bear = rng.randrange(0, 21), rng.randrange(0, 21)
                    avail = rng.choice([40, 40, 40, 30, 30, 20])
                    sig = _signal(rng, bull, bear) if avail >= 24 else "NO_TRADE"
                    eng[var] = {"signal": sig, "bull": bull, "bear": bear, "available": avail, "max": 40, "reason_codes": ([] if avail >= 24 else ["INSUFFICIENT_COVERAGE"]), "score_before_gate": sig}
                digest = hashlib.sha256(json.dumps([sym, ver, eng], sort_keys=True).encode()).hexdigest()
                preds.append({"symbol": sym, "earnings_date": ed.isoformat(), "digest": digest, "version": ver, "captured_at": (cap + timedelta(minutes=30 * (ver - 1))).isoformat(), "mode": "SIMULATION",
                              "engine": eng, "blocked_by": [], "evidence": [{"kind": "ohlc", "symbol": sym, "raw_sha256": digest, "retrieved_at": cap.isoformat()}]})
            if rng.random() < 0.85:                      # some events have no outcome yet
                outs.append({"symbol": sym, "earnings_date": ed.isoformat(), "move_pct": round(rng.gauss(0, 5), 2), "source": "simulated"})
    (store / "predictions.jsonl").write_text("\n".join(json.dumps(p) for p in preds) + "\n", encoding="utf-8")
    res = T.import_outcomes(track, outs, mode="SIMULATION", recorded_at=datetime.now(UTC) + timedelta(days=400))
    return {"predictions": len(preds), "events": weeks * per_week, "outcomes": res}
