"""Kit v6.6: the market-cap floor (7,000,000 USD). A symbol whose best known market cap is below the floor, or unknown on every
calendar observation, is dropped from the calendar observations at the one place every consumer reads them: never scanned,
never counted (not even as skipped), never displayed in the universe table, never selected for capture. Simulated provider only."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_live as L  # noqa: E402
import smart_move_live_publisher as PUB  # noqa: E402
import smart_move_calendar_tiers as CT  # noqa: E402
import smart_move_monday_bmo as MB  # noqa: E402
import smart_move_sim as S  # noqa: E402
import smart_move_universe as U  # noqa: E402
from smvshadow import governor as gv  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 11, 3, 11, 5, tzinfo=UTC)


def make(tmp_path, start=T0):
    clock = gv.FakeClock(start)
    cfg = L.LiveConfig(mode="SIMULATION", store_dir=tmp_path / "live", refresh_enabled=True, authorized_daily=1700, spacing_s=0.0)
    return L.RefreshService(cfg, S.SimulatedProvider(clock), clock), clock


def _row(sym, cap, source="company", at="2026-11-03T10:00:00+00:00"):
    return {"source": source, "symbol": sym, "date": "2026-11-04", "label": "AMC", "clock": "AMC", "vendor_flag": None, "raw_sha256": "x", "observed_at": at,
            "company": sym + " Inc", "sector": "Technology", "marketcap": cap, "request_id": "r"}


def _rewrite(svc, rows):
    lines = [json.dumps(r, sort_keys=True, default=str) for r in rows]
    (Path(svc.cfg.store_dir) / "cal_obs.jsonl").write_text(chr(10).join(lines) + chr(10), encoding="utf-8")


def test_pure_filter_rules():
    rows = [_row("BIG", "25000000000"), _row("TINY", "6999999"), _row("EDGE", "7000000"), _row("NONE", None), _row("BLANK", ""), _row("BAD", "n/a"),
            _row("MIXED", None, source="FINVIZ"), _row("MIXED", "9000000"), _row("ZERO", "0"), _row("NEG", "-5")]
    caps = L.symbol_market_caps(rows)
    assert caps == {"BIG": 25e9, "TINY": 6999999.0, "EDGE": 7e6, "MIXED": 9e6}
    ex = L.excluded_by_market_cap(rows)
    assert set(ex) == {"TINY", "NONE", "BLANK", "BAD", "ZERO", "NEG"}
    assert "below the 7000000 USD floor" in ex["TINY"] and ex["NONE"] == "market cap unknown on every calendar observation"
    kept = L.filter_cal_obs(rows)
    assert [r["symbol"] for r in kept] == ["BIG", "EDGE", "MIXED", "MIXED"]              # order preserved; the FINVIZ row of MIXED stays because the company row knows its cap
    assert L.filter_cal_obs([]) == [] and L.MIN_MARKET_CAP_USD == 7_000_000.0


def test_excluded_symbols_are_absent_from_scanning_counts_universe_selection_and_the_variant(tmp_path):
    svc, clock = make(tmp_path)
    svc.refresh_calendar([1700])                                                           # the simulated calendar: every symbol has a large cap
    before = sorted(svc._calobs())
    assert len(before) >= 5
    victims = before[:2]                                                                   # one below the floor, one with no market cap at all (a FINVIZ-only name)
    rows = svc.store.read("cal_obs")
    for r in rows:
        if r["symbol"] == victims[0]:
            r["marketcap"] = "6500000"
        elif r["symbol"] == victims[1]:
            r["marketcap"] = None
    _rewrite(svc, rows)
    assert {r["symbol"] for r in svc.store.read("cal_obs")} >= set(victims), "the raw store still holds the victims: nothing is deleted"
    seen = sorted(svc._calobs())
    assert victims[0] not in seen and victims[1] not in seen and len(seen) == len(before) - 2
    assert not {e["symbol"] for e in svc.eligible_events(clock.now())} & set(victims)      # never selected -> never captured
    scored = L.LiveScorer(svc).score_all(clock.now())
    assert not {r["symbol"] for r in scored} & set(victims)                               # never scored
    st = PUB.publish(svc, clock.now(), tmp_path / "pub", scan_id="t", mode="DRY_RUN", scan_ok=True)
    ev = (st.get("events") if isinstance(st, dict) else None) or {}
    assert ev.get("open_scanned", 0) + sum((ev.get("skipped") or {}).values()) <= len(seen), "not counted anywhere, not even as skipped"
    led = PUB.read_ledger(tmp_path / "pub" / PUB.LEDGER)
    assert not {r.get("symbol") for r in led["records"]} & set(victims)                   # never published
    uni = U.build(svc, clock.now(), horizon_days=30, slot_state=CT.load_state(tmp_path / "calstate"))
    assert not {r["symbol"] for r in uni["rows"]} & set(victims)                          # never displayed
    assert not {e["symbol"] for e in MB.events(svc, clock.now(), horizon_days=30)} & set(victims)   # the Monday-BMO variant reads the same filtered observations
