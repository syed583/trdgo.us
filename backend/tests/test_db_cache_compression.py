"""Large cache values are compressed instead of silently dropped; the storage limit is not raised."""
import json
import logging
import random
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db_cache


@pytest.fixture()
def cache(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(db_cache, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(db_cache, "_ready", False)
    monkeypatch.setattr(db_cache, "_disabled", lambda: False)
    monkeypatch.setenv("APP_ENV", "production")
    db_cache._dropped.clear()
    return db_cache


def claude_pro_like_scan(names: int = 537) -> dict:
    """A scan payload built from the real Claude Pro scorer with per-name random inputs (entropy matters:
    identical rows would compress unrealistically well)."""
    import claude_last_service as cl
    import claude_pro_service as cp
    rnd = random.Random(7)
    cl._spy_15d_return = lambda: 0.004
    biases = ["eps_estimates", "revenue_estimates", "guidance", "historical_reaction", "price_action", "sector_market",
              "options_positioning", "unusual_options", "disparity", "dark_pool", "peer_readthrough", "short_insider"]
    rows = []
    for i in range(names):
        b = {n: round(rnd.uniform(-1, 1), 3) for n in biases}
        eq = {"params": [{"name": n, "bias": b[n]} for n in biases],
              "signals": {"price_action": round(rnd.uniform(-1, 1), 3), "ema_trend": round(rnd.uniform(-1, 1), 3),
                          "options_flow": round(rnd.uniform(-1, 1), 3), "insider_activity": round(rnd.uniform(-1, 1), 3)},
              "short_interest": {"pct_float": round(rnd.uniform(0, .3), 3), "days_to_cover": round(rnd.uniform(0, 9), 2)}}
        tr = {"historical_moves": [{"date": f"2025-0{k + 1}-1{k}", "move_pct": round(rnd.uniform(-9, 9), 2)} for k in range(4)],
              "spark": [100 + rnd.uniform(-5, 5) for _ in range(30)], "expected_move": {"percent": round(rnd.uniform(2, 12), 2)}}
        r = cp.score_one(f"SYM{i:03d}", eq, tr, {"date": "2026-11-05", "company": f"Company {i} Holdings Inc."},
                         with_analyst=False, mkt=0.1)
        rows.append(r)
    return {"status": "OK", "source": cp.SOURCE, "rows": rows, "universe": names, "scored": names}


def test_small_values_stay_plain_json_and_old_rows_remain_readable():
    small = json.dumps({"a": 1})
    assert db_cache._encode(small) == small
    assert db_cache._decode(small) == {"a": 1}
    legacy_big = json.dumps({"x": "y" * 300_000})                        # a plain-JSON row written before this change
    assert db_cache._decode(legacy_big)["x"].startswith("yyy")


def test_large_value_round_trips_through_compression():
    value = {"rows": [{"symbol": f"S{i}", "score": i / 3, "note": "n" * 40} for i in range(8000)]}
    raw = json.dumps(value)
    enc = db_cache._encode(raw)
    assert len(raw) > db_cache._COMPRESS_OVER and enc.startswith(db_cache._GZ_PREFIX)
    assert len(enc) < len(raw) / 3
    assert db_cache._decode(enc) == value


def test_claude_pro_15_day_scan_is_persisted_and_read_back(cache):
    scan = claude_pro_like_scan()
    raw = json.dumps(scan, default=str)
    assert len(raw) > db_cache._MAX_BYTES, "fixture must reproduce the oversized case (~1.7 MB)"
    stored = db_cache._encode(raw)
    assert len(stored) < db_cache._MAX_BYTES / 3                          # far inside the unchanged 1.5 MB limit
    cache._write("claudepro:scan:15", scan, time.time())
    got = cache.get("claudepro:scan:15")
    assert got is not None and got[1]["universe"] == 537 and len(got[1]["rows"]) == 537
    assert got[1]["rows"][10]["symbol"] == "SYM010"
    assert cache.dropped() == {}
    print(f"claude-pro-like scan: {len(raw)} bytes raw -> {len(stored)} stored ({len(raw) / len(stored):.1f}x)")


def test_get_many_decodes_compressed_and_plain_rows(cache, monkeypatch):
    """get_many uses a PostgreSQL ANY() query that SQLite cannot run, so feed it the stored text directly."""
    big = {"rows": [{"symbol": f"S{i}", "v": i / 7} for i in range(30000)]}
    stored_big = db_cache._encode(json.dumps(big))
    assert stored_big.startswith(db_cache._GZ_PREFIX)

    class _Res:
        def all(self):
            return [("a:big", stored_big, 100.0), ("b:plain", json.dumps({"ok": 1}), 200.0), ("c:bad", "gz1:not-base64!!", 1.0)]

    class _Sess:
        def execute(self, *a, **k):
            return _Res()

        def close(self):
            pass

    monkeypatch.setattr(db_cache, "SessionLocal", lambda: _Sess())
    monkeypatch.setattr(db_cache, "_ensure_table", lambda: True)
    got = db_cache.get_many(["a:big", "b:plain", "c:bad"])
    assert got["a:big"][1] == big and got["b:plain"] == (200.0, {"ok": 1})
    assert "c:bad" not in got                                            # one corrupt row never breaks the batch


def test_value_too_large_even_compressed_is_reported_not_silent(cache, caplog):
    rnd = random.Random(1)
    noise = {"blob": "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(1_700_000))}
    with caplog.at_level(logging.WARNING, logger="db_cache"):
        cache._write("huge:key", noise, 123.0)
    assert cache.get("huge:key") is None
    d = cache.dropped()
    assert "huge:key" in d and d["huge:key"]["stored_bytes"] > cache._MAX_BYTES and d["huge:key"]["limit"] == cache._MAX_BYTES
    assert any("huge:key" in r.getMessage() and "not persisted" in r.getMessage() for r in caplog.records)
    # a later, smaller value for the same key clears the record
    cache._write("huge:key", {"ok": 1}, 124.0)
    assert "huge:key" not in cache.dropped() and cache.get("huge:key")[1] == {"ok": 1}


def test_storage_limit_is_unchanged():
    assert db_cache._MAX_BYTES == 1_500_000
