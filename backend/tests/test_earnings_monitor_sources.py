"""The nine model sources against the production code they read: cache keys, row shape, vocabulary, coverage units. Static and offline: it checks the contract between the services' own scan output and the monitor's adapter."""
import re
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from earnings_monitor import logic as L  # noqa: E402
from earnings_monitor.adapters import LABELS, MODEL_KEYS, MODEL_META, ProdAdapters  # noqa: E402
from earnings_monitor.smart_move import SmartMoveSource  # noqa: E402

UTC = timezone.utc


def routes_src():
    return (BACKEND / "api_routes.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("model", sorted(MODEL_KEYS))
def test_every_model_cache_key_is_the_one_its_own_scan_route_serves_with_the_default_window(model):
    tmpl = MODEL_KEYS[model]
    assert 'key = f"%s"' % tmpl.replace("{d}", "{d}") in routes_src() or ('swr.serve(f"%s"' % tmpl) in routes_src(), (model, tmpl)
    for m in re.finditer(r'def (\w+)_scan\(days: int = (\d+)\)', routes_src()):
        assert m.group(2) == "15"                                                      # the monitor reads the :15 keys; every scan route defaults to 15 days


@pytest.mark.parametrize("model", sorted(MODEL_META))
def test_every_model_service_builds_rows_with_the_fields_the_adapter_reads(model):
    src = (BACKEND / (MODEL_META[model]["module"] + ".py")).read_text(encoding="utf-8")
    assert "def scan(" in src
    for k in ('"signal"', '"score"', '"coverage"', '"tier"', '"symbol"', '"date"'):
        assert k in src, (model, k)
    assert '"PENDING"' in src or "PENDING" in src                                       # an unscored name is PENDING in the model's own vocabulary


def test_the_eight_services_report_coverage_as_a_percent_and_the_adapter_stores_a_fraction(monkeypatch):
    swr = types.ModuleType("swr")
    swr._values = {k.format(d=15): (datetime(2026, 11, 9, 12, 0, tzinfo=UTC).timestamp(), {"rows": [{"symbol": "AAA", "signal": "BUY", "score": 61.2, "coverage": 62.5, "tier": "Medium"},
                                                                                                  {"symbol": "BBB", "signal": "LOW_DATA", "score": None, "coverage": None},
                                                                                                  {"symbol": "CCC", "signal": "PENDING", "score": None, "coverage": None}]}) for k in MODEL_KEYS.values()}
    swr.peek = lambda key: swr._values.get(key, (0, None))[1]
    swr.held_at = lambda key: swr._values.get(key, (None, None))[0]
    monkeypatch.setitem(sys.modules, "swr", swr)
    ad = ProdAdapters(smart_move=SmartMoveSource(), session_factory=lambda: (_ for _ in ()).throw(RuntimeError("no db")), clock=lambda: datetime(2026, 11, 9, 13, 0, tzinfo=UTC))
    m = ad.models()
    assert set(m) == set(LABELS)                                                         # all nine sources present (Smart Move empty without an engine or snapshot)
    for model in MODEL_KEYS:
        a, b, c = m[model]["AAA"], m[model]["BBB"], m[model]["CCC"]
        assert a["coverage"] == 0.625 and L.coverage_fraction(a["coverage"]) == 0.625
        now = datetime(2026, 11, 9, 13, 0, tzinfo=UTC)
        assert L.model_status(a, now, 36)["status"] == "VALID" and L.model_status(b, now, 36)["status"] == "INSUFFICIENT_DATA" and L.model_status(c, now, 36)["status"] == "PENDING"
        assert L.display_status({"status": "INSUFFICIENT_DATA", "signal": "LOW_DATA"}) == "LOW_DATA"
    assert m["smart_move"] == {}


def test_the_free_text_of_no_model_source_names_an_external_provider_call():
    llm = ("anthropic", "openai", "generativeai", "api.x.ai", "moonshot", "messages.create", "chat.completions", "requests.post(")
    for model, meta in MODEL_META.items():
        text = (BACKEND / (meta["module"] + ".py")).read_text(encoding="utf-8")
        assert not [w for w in llm if w in text], (model, meta["module"])
