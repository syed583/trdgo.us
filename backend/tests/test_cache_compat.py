"""Compatibility of the monitor's cache readers with the real data shapes: field names are checked against the provider's OpenAPI spec (committed at the repository root), the read-only cache probe is exercised on
representative cache contents, and the full production-adapter path is run end to end (cache change -> evidence scan -> recompute -> stored version). No provider is contacted."""
import json
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))
from earnings_monitor.smart_move import SmartMoveSource  # noqa: E402
from earnings_monitor.sim import Clock, build_sim, write_approval  # noqa: E402

UTC = timezone.utc
SPEC = BACKEND.parent / "api_spec.yaml"


def _spec_fields(path):
    yaml = pytest.importorskip("yaml")
    if not SPEC.exists():
        pytest.skip("api_spec.yaml not present")
    spec = yaml.safe_load(SPEC.read_text(encoding="utf-8"))

    def resolve(o):
        n = 0
        while isinstance(o, dict) and "$ref" in o and n < 10:
            cur = spec
            for part in o["$ref"].lstrip("#/").split("/"):
                cur = cur[part]
            o, n = cur, n + 1
        return o

    def props(schema, depth=0):
        schema = resolve(schema)
        out = set()
        if not isinstance(schema, dict):
            return out
        for k in ("allOf", "oneOf", "anyOf"):
            for s in schema.get(k, []) or []:
                out |= props(s, depth + 1)
        for name, sub in (schema.get("properties") or {}).items():
            out.add(name)
            sub = resolve(sub)
            if depth < 3 and isinstance(sub, dict):
                out |= props(sub.get("items", {}) if sub.get("type") == "array" else sub, depth + 1)
        if schema.get("type") == "array":
            out |= props(schema.get("items", {}), depth + 1)
        return out
    op = spec["paths"][path]["get"]
    return props(resolve(op["responses"]["200"]).get("content", {}).get("application/json", {}).get("schema", {}))


def test_the_fields_the_readers_use_exist_in_the_providers_documented_responses():
    from earnings_monitor.adapters import ProdAdapters
    news = _spec_fields("/api/news/headlines")
    assert {"headline", "created_at", "sentiment", "source", "is_major"} <= news
    for p in ("/api/earnings/premarket", "/api/earnings/afterhours"):
        assert {"symbol", "report_time", "full_name", "report_date", "street_mean_est", "marketcap", "pre_earnings_close"} <= _spec_fields(p)
    assert set(ProdAdapters.NEEDS["uw_news_rows"]) <= news
    assert set(ProdAdapters.NEEDS["uw_calendar_rows"]) <= _spec_fields("/api/earnings/premarket")


def test_the_flow_readers_rely_on_the_applications_own_proven_print_parser():
    """The spec labels flow-recent with the per-expiry schema, which is a documentation mislabel (its description says 'latest flows'). The monitor therefore summarises the tape with the SAME function the
    application's Options Flow page already uses in production, so a shape problem would show there first. The cache probe reports the real fields."""
    spec = _spec_fields("/api/stock/{ticker}/flow-recent")
    assert "call_premium" in spec and "tags" not in spec
    import uw_flow_service as fs
    row = fs._print_row({"tags": ["ask_side", "sweep"], "size": 10, "price": 2.0, "option_type": "call", "premium": "2000"})
    assert row["premium"] and row["kind"] == "SWEEP"


def _fake_world(monkeypatch, *, news_fields=None, with_flow=True):
    swr = types.ModuleType("swr")
    swr._values = {}
    swr._db = {}                                                  # the shared database tier: survives a restart, the in-process dict does not
    swr.calls = {"peek": [], "peek_many": []}
    swr.peek = lambda key: swr.calls["peek"].append(key) or swr._values.get(key, (0, None))[1] or (swr._db.get(key) or (0, None))[1]
    swr.peek_many = lambda keys: swr.calls["peek_many"].append(list(keys)) or {k: (swr._values.get(k) or swr._db.get(k) or (0, None))[1] for k in keys if (swr._values.get(k) or swr._db.get(k))}
    swr.held_at = lambda key: swr._values.get(key, (None, None))[0]
    lms = types.ModuleType("live_market_service")
    lms.cache = types.SimpleNamespace(_data={})
    uw = types.ModuleType("unusualwhales_service")
    uw._rows = lambda payload: payload["data"]
    cal = types.ModuleType("uw_earnings_calendar")
    cal.TTL_FUTURE, cal.MIN_PRICE = 21600.0, 0
    cal._us_listed = lambda r: True
    cal._row = lambda r, w: {"symbol": r["symbol"], "company": r.get("full_name"), "date": w, "reporting_time": "BMO", "prior_close": None}
    for n, m in (("swr", swr), ("live_market_service", lms), ("unusualwhales_service", uw), ("uw_earnings_calendar", cal)):
        monkeypatch.setitem(sys.modules, n, m)
    now = time.time()
    swr._values["claude_ignored"] = (now, {})
    swr._values["v3:scan:v2:15"] = (now, {"rows": [{"symbol": "AAA", "signal": "NEUTRAL", "score": 50, "coverage": 80, "date": "2026-11-12", "company": "Alpha", "report_timing": "Before Open"}]})
    swr._values["earn:equity:AAA"] = (now, {"params": [{"name": "eps_estimates", "bias": 0.1, "weight": 16}, {"name": "calendar_timing", "bias": 0.0}]})
    swr._values["fviz:earn"] = (now, {"AAA": {"date": "2026-11-12", "time": "Before Market Open"}})
    lms.cache._data["uw:news:AAA:50"] = (now, {"status": "OK", "data": [dict({"headline": "Alpha raises outlook", "created_at": "2026-11-09T12:00:00Z", "sentiment": "positive", "source": "x", "is_major": True}, **(news_fields or {}))]})
    if with_flow:
        lms.cache._data["uw:flowrecent:AAA:100"] = (now, {"status": "OK", "data": [{"tags": ["ask_side"], "size": 10, "price": 2.0, "option_type": "call", "premium": "2000"}]})
    lms.cache._data["uwcal:/api/earnings/premarket:2026-11-12"] = (now, {"status": "OK", "data": [{"symbol": "AAA", "full_name": "Alpha", "report_time": "premarket"}]})
    return swr, lms


def test_the_cache_probe_reports_compatible_shapes_and_names_missing_fields(monkeypatch):
    from earnings_monitor.adapters import ProdAdapters
    _fake_world(monkeypatch)
    ad = ProdAdapters(smart_move=SmartMoveSource(), session_factory=lambda: (_ for _ in ()).throw(RuntimeError("no db")), clock=lambda: datetime(2026, 11, 9, 13, 0, tzinfo=UTC))
    p = ad.cache_probe()
    assert p["model_scan_caches"]["present"] == ["claude"] and "gemini" in p["model_scan_caches"]["absent"]
    assert p["earn_equity"]["compatible"] and "ESTIMATES" in p["earn_equity"]["evidence_kinds_mapped"] and "UNMAPPED:calendar_timing" in p["earn_equity"]["evidence_kinds_mapped"]
    assert p["uw_news"]["compatible"] and p["uw_calendar"]["compatible"] and p["finviz_cache"]["compatible"]
    assert p["uw_calendar"]["dates_cached"] == 1 and len(p["uw_calendar"]["dates_missing"]) > 10               # gaps are reported, not invented
    assert "headline" in p["uw_news"]["fields"] and not any("Alpha raises" in json.dumps(v) for v in p.values())  # shapes only: no values leak
    # a provider that drops a field is caught
    _fake_world(monkeypatch)
    sys.modules["live_market_service"].cache._data["uw:news:AAA:50"] = (time.time(), {"status": "OK", "data": [{"title": "no headline field", "created_at": "x"}]})
    bad = ProdAdapters(smart_move=SmartMoveSource(), session_factory=lambda: (_ for _ in ()).throw(RuntimeError("no db")), clock=lambda: datetime(2026, 11, 9, 13, 0, tzinfo=UTC)).cache_probe()
    assert bad["uw_news"]["compatible"] is False and "headline" in bad["uw_news"]["missing_fields"] and bad["all_readers_compatible"] is False


def test_end_to_end_with_the_production_adapter_a_changed_validated_input_recomputes_only_the_eligible_model(tmp_path, monkeypatch):
    from earnings_monitor.adapters import ProdAdapters
    swr, lms = _fake_world(monkeypatch)
    clock = Clock(datetime(2026, 11, 9, 13, 0, tzinfo=UTC))
    d = tmp_path / "em"
    d.mkdir()
    write_approval(d, clock(), mode="cache_only", days=14)
    ad = ProdAdapters(smart_move=SmartMoveSource(), session_factory=lambda: (_ for _ in ()).throw(RuntimeError("no db")), clock=clock)
    ad.model_sources = lambda: {"claude": "p = ['eps_estimates']", "grok": "p = ['price_action']"}
    store, cfg, _, mon, sched, access = build_sim(d, clock, adapters=ad)
    first = mon.eligibility_check(clock())
    assert first["observed"]["new"] == 1 and first["recompute"]["new_predictions"] == 1
    ev = store.one("SELECT * FROM events WHERE symbol='AAA'")
    assert ev["session"] == "BMO" and json.loads(ev["sources"])[0]["vendor"] == "UW" and store.one("SELECT verification FROM events")["verification"] in ("CONFIRMED", "SINGLE_VENDOR", "UNVERIFIED")
    r1 = mon.evidence_scan(clock())
    assert r1["new"] >= 2 and store.q("SELECT COUNT(*) n FROM predictions")[0]["n"] == 1                  # news and flow recorded; the unchanged input recomputed nothing
    kinds = {r["kind"]: r for r in store.q("SELECT kind, stage, relevant_models FROM evidence_items")}
    assert kinds["NEWS"]["stage"] == "COLLECTED" and kinds["ESTIMATES"]["stage"] == "VERIFIED" and json.loads(kinds["ESTIMATES"]["relevant_models"]) == ["claude"]
    # the model's cached call and its validated input move: ONE new stored version with the reason, the other eight models untouched
    clock.advance(minutes=20)
    swr._values["v3:scan:v2:15"] = (time.time(), {"rows": [{"symbol": "AAA", "signal": "BUY", "score": 72, "coverage": 80, "date": "2026-11-12", "company": "Alpha", "report_timing": "Before Open"}]})
    swr._values["earn:equity:AAA"] = (time.time(), {"params": [{"name": "eps_estimates", "bias": 0.8, "weight": 16}]})
    lms.cache._data["uw:news:AAA:50"] = (time.time(), {"status": "OK", "data": [{"headline": "Alpha lowers outlook", "created_at": "2026-11-09T13:10:00Z", "sentiment": "negative", "source": "x", "is_major": True}]})
    ad._snap = None                                                                                       # a new 20-second cache snapshot
    r2 = mon.evidence_scan(clock())
    preds = store.q("SELECT model, signal, reason FROM predictions ORDER BY id")
    assert [(p["model"], p["signal"]) for p in preds] == [("claude", "NEUTRAL"), ("claude", "BUY")] and "ESTIMATES evidence changed" in preds[1]["reason"]
    assert r2["recomputed"] == 1
    assert store.q("SELECT COUNT(*) n FROM predictions WHERE model <> 'claude'")[0]["n"] == 0             # nothing fabricated for models with no cached call
    news_changes = [json.loads(c["detail"]) for c in store.q("SELECT detail FROM changes WHERE kind IN ('EVIDENCE_NEW','EVIDENCE_CHANGED')")]
    assert any(c["kind"] == "NEWS" and c["changes_a_score"] is False for c in news_changes)               # the headline never moved a score


def _prod(monkeypatch=None):
    from earnings_monitor.adapters import ProdAdapters
    return ProdAdapters(smart_move=SmartMoveSource(), session_factory=lambda: (_ for _ in ()).throw(RuntimeError("no db")), clock=lambda: datetime(2026, 11, 9, 13, 0, tzinfo=UTC))


def test_after_a_restart_the_probe_and_the_evidence_reader_still_see_the_inputs_held_in_the_database_tier(monkeypatch):
    swr, lms = _fake_world(monkeypatch)
    entry = swr._values.pop("earn:equity:AAA")                    # the restart wiped the in-process cache ...
    swr._db["earn:equity:AAA"] = entry                            # ... but the shared database tier still holds the answer
    ad = _prod()
    p = ad.cache_probe()["earn_equity"]
    assert p["compatible"] is True and p["status"] == "COMPATIBLE" and p["symbols_cached"] == 1 and p["symbols_in_process_memory"] == 0 and p["symbols_from_database_tier"] == 1
    assert [i["ext_id"] for i in ad.evidence("AAA", "2026-11-12") if i["kind"] == "ESTIMATES"] == ["eps_estimates"]


def test_an_empty_cache_is_reported_as_no_samples_not_as_incompatible(monkeypatch):
    swr, lms = _fake_world(monkeypatch)
    swr._values.pop("earn:equity:AAA")
    p = _prod().cache_probe()
    assert p["earn_equity"]["compatible"] is None and p["earn_equity"]["status"] == "NO_SAMPLES" and p["earn_equity"]["missing_fields"] == []
    assert "earn_equity" in p["unverifiable_now"] and p["all_readers_compatible"] is True              # nothing was found WRONG; one thing could not be judged yet


def test_the_real_parameter_payload_of_the_equity_engine_matches_what_the_readers_expect(monkeypatch):
    import earnings_engine as eng
    swr, lms = _fake_world(monkeypatch)
    real = [eng.Param("eps_estimates", "EPS", 16, 0.4).as_dict(), eng.Param("options_combined", "Options", 12, -0.2).as_dict(), eng.Param("calendar_timing", "Timing", 2, None).as_dict()]
    swr._values["earn:equity:AAA"] = (time.time(), {"params": real})
    ad = _prod()
    p = ad.cache_probe()["earn_equity"]
    assert p["compatible"] is True and {"name", "bias", "weight"} <= set(p["fields"]) and "ESTIMATES" in p["evidence_kinds_mapped"] and "OPTIONS_FLOW" in p["evidence_kinds_mapped"]
    kinds = {i["kind"] for i in ad.evidence("AAA", "2026-11-12")}
    assert {"ESTIMATES", "OPTIONS_FLOW"} <= kinds                                                       # an input with no data (bias None) produces no evidence item


def test_an_evidence_scan_reads_the_equity_inputs_in_one_batch_not_one_database_trip_per_symbol(tmp_path, monkeypatch):
    swr, lms = _fake_world(monkeypatch)
    from earnings_monitor.sim import Clock, build_sim, write_approval
    clock = Clock(datetime(2026, 11, 9, 13, 0, tzinfo=UTC))
    d = tmp_path / "em"
    d.mkdir()
    write_approval(d, clock(), mode="cache_only", days=14)
    ad = _prod()
    ad.model_sources = lambda: {}
    store, cfg, _, mon, sched, access = build_sim(d, clock, adapters=ad)
    mon.eligibility_check(clock())
    swr.calls["peek"].clear()
    swr.calls["peek_many"].clear()
    mon.evidence_scan(clock())
    assert len(swr.calls["peek_many"]) == 1 and not [k for k in swr.calls["peek"] if k.startswith("earn:equity:")]


def test_a_calendar_read_asks_for_the_finviz_map_once_not_once_per_event(monkeypatch):
    swr, lms = _fake_world(monkeypatch)
    for i in range(200):                                                                    # 200 cached UW rows
        lms.cache._data["uwcal:/api/earnings/premarket:2026-11-1%d" % (i % 3)] = (time.time(), {"status": "OK", "data": [{"symbol": "S%03d" % j, "full_name": "n", "report_time": "premarket"} for j in range(200)]})
    swr.calls["peek"].clear()
    rows = _prod().calendar()
    assert len([r for r in rows if r["source"] == "uw_calendar_cache"]) >= 200
    assert swr.calls["peek"].count("fviz:earn") == 1                                        # in production each extra peek is a database round trip plus a JSON parse
