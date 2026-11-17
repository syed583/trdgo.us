"""G: a user's order goes only to a symbol THEY mapped at THEIR broker.

Owner decision 2026-11-17, superseding 2026-11-16: for users, their own verified mapping replaces the
operator's hard-coded exclusion list. The operator's own book keeps that list unchanged.

Why mapping rather than guessing: brokers spell the same company differently (AAPL, AAPL.m, AAPL.US), and a
near-miss suffix can be a DIFFERENT instrument - which is how the operator's book took wrong-symbol fills in
A.18. C.2 guessed by probing suffixes; G does not guess at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import user_copy_service as CP                                   # noqa: E402
import user_mt5_routes as R                                      # noqa: E402
import user_mt5_service as usvc                                  # noqa: E402

TOKEN = "USER-TOKEN"


def _code_of(path, fn_name):
    """A function's CODE, with its docstring dropped.

    These checks look for techniques we must not use - suffix guessing, prefix matching. Searching the raw
    text matched the comments that explain why we avoid them, which is the test failing on its own argument.
    """
    import ast as _ast
    tree = _ast.parse(path.read_text(encoding="utf-8"))
    fn = [n for n in _ast.walk(tree) if isinstance(n, _ast.FunctionDef) and n.name == fn_name][0]
    fn.body = [st for st in fn.body
               if not (isinstance(st, _ast.Expr) and isinstance(st.value, _ast.Constant))]
    return _ast.unparse(fn)


# --------------------------------------------------------------------------- the mapping decides
def test_the_copy_path_never_guesses_a_symbol():
    code = _code_of(BACKEND / "user_copy_service.py", "_broker_symbol")
    assert "symmap.get" in code, "the user's own mapping must decide"
    for guess in ("_suffixes", "AT.mapped", "bridge.symbol("):
        assert guess not in code, "the copy path still guesses with %s" % guess


def test_an_unmapped_symbol_is_skipped_not_guessed(monkeypatch):
    logged = []
    monkeypatch.setattr(CP, "_safe_log", lambda u, e, **k: logged.append((e, k.get("reason"))))
    monkeypatch.setattr(CP.bridge, "available", lambda: True)
    monkeypatch.setattr(CP.usvc, "session_for", lambda u: (TOKEN, None))
    monkeypatch.setattr(CP, "_entry_refused", lambda u, ev, t: None)
    monkeypatch.setattr(CP.symmap, "get", lambda u, s: None)
    out = CP._open_for({"username": "ann", "shares": 10, "max_positions": 20, "daily_loss_pct": 1.0},
                       {"type": "ENTRY", "trade_id": "T1", "symbol": "AMD", "side": "BUY", "entry": 100.0,
                        "stop": 99.0, "target": 101.0, "strategy": "gemini"})
    assert out["reason"] == "NOT_MAPPED"
    event, reason = logged[0]
    assert event == "SKIPPED" and "AMD" in reason and "My Broker" in reason


def test_a_mapping_without_volume_rules_is_refused(monkeypatch):
    """A mapping we cannot size with is a skip waiting to happen at the worst moment."""
    monkeypatch.setattr(CP.symmap, "get", lambda u, s: {"broker_symbol": "AMD.m", "contract_size": 100.0,
                                                        "volume_min": None, "volume_step": None})
    bsym, why = CP._broker_symbol("ann", TOKEN, "AMD")
    assert bsym is None and why == "NOT_MAPPED"


def test_the_mapped_name_and_its_rules_are_used(monkeypatch):
    monkeypatch.setattr(CP.symmap, "get", lambda u, s: {"broker_symbol": "AMD.m", "contract_size": 1.0,
                                                        "volume_min": 1.0, "volume_step": 1.0,
                                                        "volume_max": 500.0})
    bsym, info = CP._broker_symbol("ann", TOKEN, "AMD")
    assert bsym == "AMD.m"
    assert info["contract_size"] == 1.0 and info["volume_step"] == 1.0


# --------------------------------------------------------------------------- the exclusion list, per owner
def test_users_are_not_bound_by_the_operators_exclusion_list():
    src = (BACKEND / "user_copy_service.py").read_text(encoding="utf-8")
    assert "AT.is_excluded" not in src


def test_the_operators_own_list_is_untouched():
    admin = (BACKEND / "mt5_autotrade.py").read_text(encoding="utf-8")
    # H dropped the bogus "ORACLE" entry, so the list is not pinned literally - what matters is that the
    # operator still HAS a list and it still gates our own orders.
    import mt5_autotrade as _AT
    assert set(_AT.DEFAULT_EXCLUDED) >= {"LLY", "COP", "MU", "SPY"}, "the operator's losers must stay listed"
    assert "ORACLE" not in _AT.DEFAULT_EXCLUDED, "ORACLE is not a ticker and excluded nothing"
    block = admin.split("def _entry_allowed")[1].split("\ndef ")[0]
    assert 'return "EXCLUDED"' in block


# --------------------------------------------------------------------------- search is on the user's session
def test_the_symbol_list_is_fetched_with_the_users_own_token():
    """symbols_list() had no token at all - it would have shown every user the OPERATOR's symbols."""
    bridge_src = (BACKEND / "mt5_bridge.py").read_text(encoding="utf-8")
    sig = bridge_src.split("def symbols_list(")[1].split("-> dict:")[0]
    assert "token" in sig and "strict_token" in sig
    svc_src = (BACKEND / "user_mt5_service.py").read_text(encoding="utf-8")
    assert "bridge.symbols_list(token=token, strict_token=True)" in svc_src


def test_search_orders_exact_then_prefix_then_contains(monkeypatch):
    monkeypatch.setattr(usvc, "broker_symbols",
                        lambda u, refresh=False: (["XAMDY", "AMD.m", "AMD", "AMDX"], None))
    out = usvc.search_symbols("ann", "amd")
    assert out["matches"][0] == "AMD", "an exact match must come first"
    assert out["matches"][1:3] == ["AMD.m", "AMDX"] or out["matches"][1:3] == ["AMDX", "AMD.m"]
    assert "XAMDY" in out["matches"]


def test_an_empty_search_is_refused(monkeypatch):
    assert usvc.search_symbols("ann", "  ")["reason"] == "EMPTY"


def test_a_refused_session_stops_the_search(monkeypatch):
    monkeypatch.setattr(usvc, "session_for",
                        lambda u: (None, {"ok": False, "reason": "SESSION_ACCOUNT_MISMATCH"}))
    monkeypatch.setattr(usvc, "_symbols_cache", {})
    names, err = usvc.broker_symbols("ann")
    assert names is None and err["reason"] == "SESSION_ACCOUNT_MISMATCH"


# --------------------------------------------------------------------------- map-auto is exact-only
def test_map_auto_only_takes_exact_matches(monkeypatch):
    """Anything needing a suffix is left to the user: a near-miss suffix can be another instrument."""
    code = _code_of(BACKEND / "user_mt5_routes.py", "map_auto")
    assert "at_broker.get(sym.upper())" in code
    for guess in ("suffix", "startswith", "_suffixes"):
        assert guess not in code, "map-auto must not guess with %s" % guess


def test_mapping_verifies_with_the_broker_before_saving(monkeypatch):
    saved = {}
    monkeypatch.setattr(R, "_me", lambda request: "ann")
    monkeypatch.setattr(R.svc, "verify_symbol",
                        lambda u, b: {"ok": False, "reason": "NOT_FOUND", "detail": "no such symbol"})
    monkeypatch.setattr(R.symmap, "save", lambda *a, **k: saved.setdefault("called", True))
    out = R.map_symbol(None, payload={"symbol": "AMD", "broker_symbol": "NOPE"})
    assert out["ok"] is False and out["reason"] == "NOT_FOUND"
    assert "called" not in saved, "an unverified symbol must never be stored"


def test_mapping_stores_the_brokers_own_rules(monkeypatch):
    got = {}
    monkeypatch.setattr(R, "_me", lambda request: "ann")
    monkeypatch.setattr(R.svc, "verify_symbol", lambda u, b: {"ok": True, "info": {"contract_size": 1.0,
                                                                                   "volume_min": 1.0,
                                                                                   "volume_step": 1.0}})
    monkeypatch.setattr(R.symmap, "save",
                        lambda u, s, b, info: got.update(u=u, s=s, b=b, info=info) or {"ok": True})
    R.map_symbol(None, payload={"symbol": "amd", "broker_symbol": "AMD.m"})
    assert got["s"] == "AMD" and got["b"] == "AMD.m"
    assert got["info"]["volume_step"] == 1.0, "the broker's rules must be stored with the mapping"


# --------------------------------------------------------------------------- the routes are per-user
def test_every_mapping_route_takes_the_user_from_the_session():
    src = (BACKEND / "user_mt5_routes.py").read_text(encoding="utf-8")
    user_half = src.split("admin_router = APIRouter")[0]
    block = user_half.split("G: symbol mapping")[1]
    assert block.count("_me(request)") >= 5, "every mapping route must read the user from the session"
    assert 'payload.get("username")' not in block


def test_the_store_refuses_a_mapping_with_no_volume_rules():
    import user_symbol_map as symmap
    out = symmap.save("ann", "AMD", "AMD.m", {"contract_size": 1.0})
    assert out["ok"] is False and out["reason"] == "NO_VOLUME_RULES"


# --------------------------------------------------------------------------- display is never filtered
def test_the_exclusion_list_never_touches_a_display_path():
    """Owner rule: every universe symbol always shows its signals; the list may only block the MT5 send."""
    import subprocess
    hits = subprocess.run(["git", "-C", str(BACKEND.parent), "grep", "-ln",
                           "is_excluded\\|excluded_symbols", "--", "backend/*.py"],
                          capture_output=True, text=True).stdout.split()
    allowed = {"backend/mt5_autotrade.py", "backend/user_copy_service.py"}
    unexpected = [h for h in hits if h not in allowed and "test" not in h]
    assert unexpected == [], "exclusions reached a new module - check it is not a display path: %s" % unexpected
