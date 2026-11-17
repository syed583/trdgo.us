"""C.2: the four things the owner asked to be proven, plus the rails.

    1. one user cannot affect another's account;
    2. an order is placed only for a user with Auto-copy ON;
    3. the same share setting gives the right lot at brokers with DIFFERENT contract sizes;
    4. a failure on one user's account never blocks our admin account or the other users.

The broker is a fake keyed by session token, so "whose account was this order placed in" is checkable: every
order lands in the book of the token it was sent with. A test that could not tell the accounts apart would not
be testing isolation at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mt5_autotrade as AT                                       # noqa: E402
import user_copy_service as CP                                   # noqa: E402
import user_mt5_store as store                                   # noqa: E402


# --------------------------------------------------------------------------- a broker with per-account books
class FakeBroker:
    """Two accounts with different contract sizes, so one share setting must produce two different lots."""

    def __init__(self):
        self.accounts = {
            "tok-ann": {"contract_size": 100.0, "volume_step": 0.01, "volume_min": 0.01, "volume_max": 50.0,
                        "equity": 10000.0, "suffix": "", "fail": False},
            "tok-bob": {"contract_size": 1.0, "volume_step": 1.0, "volume_min": 1.0, "volume_max": 500.0,
                        "equity": 20000.0, "suffix": ".m", "fail": False},
        }
        self.orders = {k: [] for k in self.accounts}
        self.closes = {k: [] for k in self.accounts}
        self.next_ticket = 1000

    def _acct(self, token, strict_token=False):
        # F: a strict call with no token must never reach an account. In the real bridge this used to fall
        # through to the OPERATOR's session, which is how 25 user positions were opened in 250086.
        if strict_token and not token:
            raise AssertionError("a user call reached the broker with NO session token")
        a = self.accounts.get(token)
        if a is None:
            raise AssertionError("an order was sent with no account token: %r" % token)
        if a["fail"]:
            raise RuntimeError("this broker is down")
        return a

    def symbol(self, sym, select=True, token=None, strict_token=False):
        a = self._acct(token, strict_token)
        if not sym.endswith(a["suffix"]) or sym[:len(sym) - len(a["suffix"]) if a["suffix"] else len(sym)] == "":
            return {"ok": False, "reason": "NOT_FOUND"}
        return {"ok": True, "symbol": sym, "contract_size": a["contract_size"], "volume_min": a["volume_min"],
                "volume_step": a["volume_step"], "volume_max": a["volume_max"]}

    def tick(self, sym, token=None, strict_token=False):
        self._acct(token, strict_token)
        return {"ok": True, "bid": 99.9, "ask": 100.1}

    def market_order(self, sym, side, volume, *, sl, tp, deviation_points, magic, comment,
                     sym_info=None, check_only=False, token=None, strict_token=False):
        self._acct(token, strict_token)
        self.next_ticket += 1
        self.orders[token].append({"symbol": sym, "side": side, "volume": volume, "sl": sl, "tp": tp,
                                   "ticket": self.next_ticket})
        fill = 100.1 if side in ("BUY", "LONG") else 99.9      # a buy pays the ask, a sell gets the bid
        return {"ok": True, "order": self.next_ticket, "volume": volume, "price": fill}

    def close_position(self, ticket, *, deviation_points, magic, comment, token=None,
                       strict_token=False):
        self._acct(token, strict_token)
        self.closes[token].append(int(ticket))
        return {"ok": True, "price": 102.0, "volume": 1.0}

    def _call(self, path, params=None, *, with_token=True, base_url=None, token=None,
              strict_token=False):
        a = self._acct(token, strict_token)
        if path == "AccountSummary":
            return {"ok": True, "data": {"equity": a["equity"], "balance": a["equity"]}}
        return {"ok": False, "reason": "UNEXPECTED_CALL", "detail": path}

    @staticmethod
    def available():
        return True

    _f = staticmethod(lambda v: None if v is None else float(v))

    @staticmethod
    def _g(d, *keys):
        for k in keys:
            if k in (d or {}):
                return d[k]
        return None


# --------------------------------------------------------------------------- a store in memory
class FakeStore:
    DEFAULT_SHARES = 10
    DEFAULT_MAX_POSITIONS = 20
    DEFAULT_DAILY_LOSS_PCT = 1.0

    def __init__(self, users):
        self.users = users               # [{username, shares, max_positions, daily_loss_pct}]
        self.log = []
        self.open: dict = {}
        self.realized: dict = {}

    def copying_users(self):
        return [dict(u) for u in self.users]

    def log_order(self, username, event, **cols):
        self.log.append(dict(cols, username=username, event=event))
        if event == "FILLED":
            self.open.setdefault(username, []).append(
                {"admin_trade_id": cols.get("admin_trade_id"), "ticket": cols.get("ticket"),
                 "symbol": cols.get("symbol"), "broker_symbol": cols.get("broker_symbol"),
                 "side": cols.get("side"), "shares": cols.get("shares"), "volume": cols.get("volume"),
                 "price": cols.get("price"), "strategy": cols.get("strategy")})
        if event == "CLOSED":
            rows = self.open.get(username) or []
            self.open[username] = [r for r in rows
                                   if str(r["admin_trade_id"]) != str(cols.get("admin_trade_id"))]

    def open_tickets(self, username):
        return list(self.open.get(username) or [])

    def open_row(self, username, tid):
        for r in self.open_tickets(username):
            if str(r["admin_trade_id"]) == str(tid):
                return r
        return None

    def realized_today(self, username):
        return float(self.realized.get(username, 0.0))

    def settings_of(self, username):
        for u in self.users:
            if u["username"] == username:
                return dict(u, auto_copy=True, enabled=True, admin_paused=False, has_account=True)
        return {"has_account": False, "shares": self.DEFAULT_SHARES}

    def events(self, username, event=None):
        return [r for r in self.log
                if r["username"] == username and (event is None or r["event"] == event)]


ENTRY = {"type": "ENTRY", "trade_id": "T1", "strategy": "gemini", "symbol": "AAPL", "side": "BUY",
         "shares": 7, "entry": 100.0, "stop": 99.0, "target": 101.0}
EXIT = {"type": "EXIT", "trade_id": "T1", "symbol": "AAPL", "reason": "SCORE_EXIT", "exit_price": 102.0}


@pytest.fixture
def rig(monkeypatch):
    broker = FakeBroker()
    st = FakeStore([{"username": "ann", "shares": 100, "max_positions": 20, "daily_loss_pct": 1.0},
                    {"username": "bob", "shares": 100, "max_positions": 20, "daily_loss_pct": 1.0}])
    monkeypatch.setattr(CP, "bridge", broker)
    monkeypatch.setattr(CP, "store", st)
    monkeypatch.setattr(CP, "_sym_cache", {})
    monkeypatch.setattr(CP, "_equity_cache", {})
    monkeypatch.setattr(CP, "MAX_ORDERS_PER_SECOND", 1000.0)      # the rate limit has its own test
    monkeypatch.setattr(CP.usvc, "session_for", lambda u: ("tok-%s" % u, None))
    monkeypatch.setattr(CP.AT, "is_excluded", lambda s: False)
    monkeypatch.setattr(CP.AT, "mapped", lambda s: None)
    monkeypatch.setattr(CP.AT, "_suffixes", lambda: ["", ".m", "US.m"])
    monkeypatch.setattr(CP.dts, "stop_cap_reason", lambda entry, sl: None)
    return broker, st


# --------------------------------------------------------------------------- 1. one user cannot touch another
def test_each_order_lands_in_its_own_account(rig):
    broker, st = rig
    CP.dispatch(ENTRY)
    assert len(broker.orders["tok-ann"]) == 1
    assert len(broker.orders["tok-bob"]) == 1
    # and each is the symbol as THAT broker names it
    assert broker.orders["tok-ann"][0]["symbol"] == "AAPL"
    assert broker.orders["tok-bob"][0]["symbol"] == "AAPL.m"


def test_a_close_only_closes_that_users_ticket(rig):
    broker, st = rig
    CP.dispatch(ENTRY)
    ann_ticket = broker.orders["tok-ann"][0]["ticket"]
    bob_ticket = broker.orders["tok-bob"][0]["ticket"]
    assert ann_ticket != bob_ticket
    CP.dispatch(EXIT)
    assert broker.closes["tok-ann"] == [ann_ticket]
    assert broker.closes["tok-bob"] == [bob_ticket]


def test_one_users_session_is_never_used_for_another(rig, monkeypatch):
    broker, st = rig
    monkeypatch.setattr(CP.usvc, "session_for",
                        lambda u: (("tok-ann", None) if u == "ann" else (None, {"ok": False, "reason": "DEAD"})))
    CP.dispatch(ENTRY)
    assert len(broker.orders["tok-ann"]) == 1
    assert broker.orders["tok-bob"] == []                       # bob has no session: nothing sent on anyone's
    # F: the log now names the SPECIFIC refusal (DEAD, SESSION_ACCOUNT_MISMATCH, OPERATOR_ACCOUNT ...)
    # instead of a generic NO_SESSION, so an operator can see which rail stopped the order.
    assert st.events("bob", "SKIPPED")[0]["reason"].startswith("DEAD")


# --------------------------------------------------------------------------- 2. only with Auto-copy ON
def test_only_users_with_auto_copy_on_are_dispatched(rig):
    broker, st = rig
    # copying_users() is the ONLY list dispatch reads, and the store builds it from
    # auto_copy = TRUE AND enabled = TRUE AND admin_paused = FALSE AND consent_at IS NOT NULL
    st.users = [{"username": "ann", "shares": 100, "max_positions": 20, "daily_loss_pct": 1.0}]
    CP.dispatch(ENTRY)
    assert len(broker.orders["tok-ann"]) == 1
    assert broker.orders["tok-bob"] == []


def test_the_copying_query_requires_all_four_switches():
    """The SQL itself, so a later edit cannot quietly widen who gets orders."""
    src = (Path(CP.store.__file__ if hasattr(CP.store, "__file__") else "").parent
           / "user_mt5_store.py") if False else Path(store.__file__)
    sql = src.read_text(encoding="utf-8")
    where = sql.split("def copying_users")[1].split("def ")[0]
    for clause in ("auto_copy = TRUE", "enabled = TRUE", "admin_paused = FALSE", "consent_at IS NOT NULL"):
        assert clause in where, "copying_users no longer requires %s" % clause


# --------------------------------------------------------------------------- 3. same setting, different brokers
def test_one_share_setting_gives_the_right_lot_at_each_broker(rig):
    broker, st = rig
    CP.dispatch(ENTRY)
    # 100 shares: contract size 100 -> 1.0 lot; contract size 1 -> 100.0 lots. Same setting, different lot.
    assert broker.orders["tok-ann"][0]["volume"] == pytest.approx(1.0)
    assert broker.orders["tok-bob"][0]["volume"] == pytest.approx(100.0)


def test_a_share_count_off_the_volume_grid_is_skipped_not_rounded(rig):
    broker, st = rig
    st.users = [{"username": "ann", "shares": 150, "max_positions": 20, "daily_loss_pct": 1.0}]
    # 150 shares / contract size 100 = 1.5 lots, which IS on a 0.01 step: valid.
    CP.dispatch(ENTRY)
    assert broker.orders["tok-ann"][0]["volume"] == pytest.approx(1.5)
    # now a step that cannot express it
    broker.accounts["tok-ann"]["volume_step"] = 1.0
    broker.accounts["tok-ann"]["volume_min"] = 1.0
    CP.forget_symbols("ann")
    CP.dispatch(dict(ENTRY, trade_id="T2"))
    skipped = [r for r in st.events("ann", "SKIPPED") if (r["reason"] or "").startswith("SKIPPED_SIZE")]
    assert skipped, "150 shares on a 1.0-lot step should be SKIPPED_SIZE"
    assert len(broker.orders["tok-ann"]) == 1                   # and no second order was sent


def test_the_users_shares_are_used_not_our_paper_shares(rig):
    broker, st = rig
    st.users = [{"username": "bob", "shares": 20, "max_positions": 20, "daily_loss_pct": 1.0}]
    CP.dispatch(ENTRY)                                          # the event carries shares=7; it must be ignored
    assert broker.orders["tok-bob"][0]["volume"] == pytest.approx(20.0)


def test_a_symbol_the_users_broker_does_not_offer_is_skipped(rig):
    broker, st = rig
    broker.accounts["tok-bob"]["suffix"] = "_NOPE"
    CP.forget_symbols()
    CP.dispatch(ENTRY)
    assert broker.orders["tok-bob"] == []
    assert any(r["reason"].startswith("NOT_AT_BROKER") for r in st.events("bob", "SKIPPED"))


# --------------------------------------------------------------------------- 4. one failure blocks nothing
def test_a_broken_broker_for_one_user_does_not_stop_the_others(rig):
    broker, st = rig
    broker.accounts["tok-ann"]["fail"] = True
    CP.dispatch(ENTRY)
    assert len(broker.orders["tok-bob"]) == 1                   # bob still traded
    assert st.events("ann", "ERROR"), "ann's failure should be recorded"


def test_the_tracker_hook_never_raises(monkeypatch):
    """Our own book runs in the same hook list. A copy failure must not propagate into the tracker."""
    monkeypatch.setattr(CP, "_queue", __import__("collections").deque())
    CP.on_paper_event(None)                                     # no type: ignored
    CP.on_paper_event({"type": "ENTRY", "symbol": "AAPL"})
    assert CP.queue_depth() == 1


def test_a_queue_failure_is_swallowed(monkeypatch):
    class Boom:
        def __len__(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(CP, "_queue", Boom())
    CP.on_paper_event({"type": "ENTRY", "symbol": "AAPL"})      # must not raise


def test_the_copy_hook_is_registered_after_ours():
    """Our order goes first; the copies follow. Read from main.py so the order cannot drift."""
    src = (Path(store.__file__).parent / "main.py").read_text(encoding="utf-8")
    ours = src.index("dts.HOOKS.append(AT.on_paper_event)")
    theirs = src.index("dts.HOOKS.append(CP.on_paper_event)")
    assert ours < theirs


# --------------------------------------------------------------------------- the per-user rails
def test_max_open_positions_stops_a_new_order(rig):
    broker, st = rig
    st.users = [{"username": "ann", "shares": 100, "max_positions": 1, "daily_loss_pct": 1.0}]
    CP.dispatch(ENTRY)
    CP.dispatch(dict(ENTRY, trade_id="T2"))
    assert len(broker.orders["tok-ann"]) == 1
    assert any(r["reason"] == "MAX_OPEN_POSITIONS" for r in st.events("ann", "SKIPPED"))


def test_the_daily_loss_limit_stops_new_orders(rig):
    broker, st = rig
    st.users = [{"username": "ann", "shares": 100, "max_positions": 20, "daily_loss_pct": 1.0}]
    st.realized["ann"] = -120.0                                 # equity 10,000; the 1% limit is 100
    CP.dispatch(ENTRY)
    assert broker.orders["tok-ann"] == []
    assert any(r["reason"] == "DAILY_LOSS_LIMIT" for r in st.events("ann", "SKIPPED"))


def test_a_profit_does_not_trip_the_daily_limit(rig):
    broker, st = rig
    st.users = [{"username": "ann", "shares": 100, "max_positions": 20, "daily_loss_pct": 1.0}]
    st.realized["ann"] = 500.0
    CP.dispatch(ENTRY)
    assert len(broker.orders["tok-ann"]) == 1


def test_an_unreadable_equity_fails_closed(rig):
    broker, st = rig
    st.users = [{"username": "ann", "shares": 100, "max_positions": 20, "daily_loss_pct": 1.0}]
    st.realized["ann"] = -50.0
    broker.accounts["tok-ann"]["equity"] = None
    CP.dispatch(ENTRY)
    assert broker.orders["tok-ann"] == []
    assert any(r["reason"] == "EQUITY_UNKNOWN" for r in st.events("ann", "SKIPPED"))


def test_the_stop_cap_is_the_same_rail_our_book_uses(rig, monkeypatch):
    broker, st = rig
    monkeypatch.setattr(CP.dts, "stop_cap_reason", lambda entry, sl: "STOP_CAP: too far")
    CP.dispatch(ENTRY)
    assert broker.orders["tok-ann"] == [] and broker.orders["tok-bob"] == []
    assert any(r["reason"].startswith("STOP_CAP") for r in st.events("ann", "SKIPPED"))


def test_the_stop_cap_rail_is_not_a_copy_of_the_number(rig):
    """It must CALL demo_trade_service, so changing the cap once changes it everywhere."""
    import ast
    src = Path(CP.__file__).read_text(encoding="utf-8")
    assert "dts.stop_cap_reason" in src
    code = ast.unparse(ast.parse(src))                          # docstrings keep the words, not the number
    numbers = [n.value for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.Constant) and isinstance(n.value, float)]
    assert 2.5 not in numbers, "the cap must not be hard-coded here: %s" % numbers
    assert "max_stop_pct" not in code, "the cap must be read through demo_trade_service, not re-derived"


def test_an_excluded_symbol_is_not_copied(rig, monkeypatch):
    broker, st = rig
    monkeypatch.setattr(CP.AT, "is_excluded", lambda s: s == "AAPL")
    CP.dispatch(ENTRY)
    assert broker.orders["tok-ann"] == []
    assert any(r["reason"] == "EXCLUDED" for r in st.events("ann", "SKIPPED"))


def test_the_rate_limit_holds_orders_apart(monkeypatch):
    """F replaced C.2's user-only limiter with broker_rate, the single gate the operator's book shares.

    A user-only limiter never bounded anything: the broker counts orders per second across the whole
    account, so the admin engine's orders went out beside the user's and the total broke the limit.
    """
    import time as _t
    import broker_rate
    monkeypatch.setattr(broker_rate, "MAX_PER_SECOND", 2.0)     # tests otherwise run the gate wide open
    broker_rate.reset()
    t0 = _t.time()
    CP._throttle()
    CP._throttle()
    CP._throttle()
    assert _t.time() - t0 >= 0.95, "three orders at 2/s should take at least ~1s"


# --------------------------------------------------------------------------- the log and the P&L
def test_the_log_records_the_fill_slippage_and_pnl(rig):
    broker, st = rig
    st.users = [{"username": "ann", "shares": 100, "max_positions": 20, "daily_loss_pct": 1.0}]
    CP.dispatch(ENTRY)
    filled = st.events("ann", "FILLED")[0]
    assert filled["ticket"] and filled["price"] == 100.1
    assert filled["slippage"] == pytest.approx(0.0)             # filled at the quoted ask
    CP.dispatch(EXIT)
    closed = st.events("ann", "CLOSED")[0]
    # bought at 100.1, closed at 102.0, 100 shares
    assert closed["pnl"] == pytest.approx(190.0)
    assert closed["reason"] == "SCORE_EXIT"


def test_a_close_for_a_user_we_never_opened_is_a_no_op(rig):
    broker, st = rig
    CP.dispatch(EXIT)
    assert broker.closes["tok-ann"] == [] and broker.closes["tok-bob"] == []
    assert st.log == []


def test_a_short_is_copied_as_a_short_and_priced_correctly(rig):
    broker, st = rig
    st.users = [{"username": "ann", "shares": 100, "max_positions": 20, "daily_loss_pct": 1.0}]
    CP.dispatch(dict(ENTRY, side="SELL", entry=100.0, stop=101.0, target=99.0))
    assert broker.orders["tok-ann"][0]["side"] == "SELL"
    assert broker.orders["tok-ann"][0]["sl"] == 101.0
    CP.dispatch(EXIT)
    # sold at 99.9 (the bid), closed at 102.0: a loss of 2.10 a share
    assert st.events("ann", "CLOSED")[0]["pnl"] == pytest.approx(-210.0)


def test_the_preview_shows_each_users_own_lot(rig, monkeypatch):
    broker, st = rig
    monkeypatch.setattr(CP.AT, "software_symbols", lambda: ["AAPL", "MSFT"])
    monkeypatch.setattr(CP.AT, "excluded_symbols", lambda: [])
    ann = CP.preview("ann")
    bob = CP.preview("bob")
    assert ann["shares"] == 100 and bob["shares"] == 100
    assert ann["rows"][0]["volume"] == pytest.approx(1.0)
    assert bob["rows"][0]["volume"] == pytest.approx(100.0)
    assert bob["rows"][0]["broker_symbol"] == "AAPL.m"


def test_the_users_own_status_says_whether_it_is_live(rig):
    broker, st = rig
    out = CP.user_status("ann")
    assert out["stage"] == "C.2" and out["live"] is True and out["copying"] is True


# --------------------------------------------------------------------------- sizing, direct
@pytest.mark.parametrize("contract,step,vmin,shares,expect", [
    (100.0, 0.01, 0.01, 10, 0.1),
    (100.0, 0.01, 0.01, 100, 1.0),
    (1.0, 1.0, 1.0, 10, 10.0),
    (1.0, 0.1, 0.1, 50, 50.0),
    (10.0, 0.1, 0.1, 30, 3.0),
])
def test_sizing_is_exact_for_each_contract_size(contract, step, vmin, shares, expect):
    z = AT.sizing(contract, vmin, step, 1000.0, shares)
    assert z["valid"] and z["volume"] == pytest.approx(expect)
    assert z["shares_sent"] == pytest.approx(shares)


@pytest.mark.parametrize("contract,step,vmin,shares", [
    (3.0, 1.0, 1.0, 10),          # 10/3 is not a whole lot
    (100.0, 1.0, 1.0, 10),        # 0.1 lots is below a 1.0 step
    (0.0, 1.0, 1.0, 10),          # contract size unknown
])
def test_sizing_refuses_rather_than_sending_a_different_size(contract, step, vmin, shares):
    z = AT.sizing(contract, vmin, step, 1000.0, shares)
    assert not z["valid"] and z["reason"]


# --------------------------------------------------------------------------- the switches cannot start by themselves
def test_auto_copy_cannot_be_switched_on_without_consent(monkeypatch):
    import user_mt5_routes as R
    monkeypatch.setattr(R, "_me", lambda request: "ann")
    monkeypatch.setattr(R.store, "get", lambda u: {"username": u, "consent_at": None})
    out = R.set_copy_settings(None, {"auto_copy": True})
    assert out["ok"] is False and out["reason"] == "NO_CONSENT"


def test_auto_copy_cannot_be_switched_on_without_an_account(monkeypatch):
    import user_mt5_routes as R
    monkeypatch.setattr(R, "_me", lambda request: "ann")
    monkeypatch.setattr(R.store, "get", lambda u: None)
    out = R.set_copy_settings(None, {"auto_copy": True})
    assert out["ok"] is False and out["reason"] == "NO_ACCOUNT"


def test_switching_auto_copy_off_needs_neither(monkeypatch):
    import user_mt5_routes as R
    seen = {}
    monkeypatch.setattr(R, "_me", lambda request: "ann")
    monkeypatch.setattr(R.store, "update_settings", lambda u, p: seen.setdefault("p", p) or {"ok": True})
    R.set_copy_settings(None, {"auto_copy": False})
    assert seen["p"] == {"auto_copy": False}


def test_the_settings_route_ignores_keys_it_does_not_own(monkeypatch):
    import user_mt5_routes as R
    seen = {}
    monkeypatch.setattr(R, "_me", lambda request: "ann")
    monkeypatch.setattr(R.store, "update_settings", lambda u, p: seen.setdefault("p", p) or {"ok": True})
    R.set_copy_settings(None, {"shares": 50, "username": "bob", "enabled": True, "admin_paused": False})
    assert seen["p"] == {"shares": 50}, "only the user's own four settings may be patched"


def test_a_user_route_takes_the_username_from_the_session_only():
    """No C.2 user route may read a username from the body or the path."""
    src = Path(Path(store.__file__).parent / "user_mt5_routes.py").read_text(encoding="utf-8")
    user_half = src.split("admin_router = APIRouter")[0]
    assert 'payload.get("username")' not in user_half
    assert "{username}" not in user_half


def test_the_admin_routes_are_operator_only():
    src = Path(Path(store.__file__).parent / "user_mt5_routes.py").read_text(encoding="utf-8")
    admin_half = src.split("admin_router = APIRouter")[1]
    for fn in ("def copy_users", "def pause_user", "def user_orders"):
        body = admin_half.split(fn)[1].split("\n@")[0]
        assert "_operator(request)" in body, "%s is not operator-gated" % fn


# --------------------------------------------------------------------------- the log must not name a model
def test_the_users_order_log_shows_agent_labels_not_model_names(monkeypatch):
    import user_mt5_routes as R
    rows = [{"strategy": "gemini", "symbol": "AAPL", "reason": "price from Unusual Whales, scored by grok"},
            {"strategy": "meta", "symbol": "MSFT", "reason": None}]
    monkeypatch.setattr(R, "_me", lambda request: "ann")
    monkeypatch.setattr(R.store, "orders_of", lambda u, n: [dict(r) for r in rows])
    out = R.my_orders(None, 10)["orders"]
    assert out[0]["strategy"] == "Agent 4" and out[1]["strategy"] == "Agent 2"
    blob = str(out)
    for word in ("gemini", "Gemini", "grok", "Grok", "meta", "Unusual Whales"):
        assert word not in blob, "%s reached a normal user's order log" % word


def test_the_admin_order_log_keeps_the_real_model_name(monkeypatch):
    import user_mt5_routes as R
    monkeypatch.setattr(R, "_operator", lambda request: None)
    monkeypatch.setattr(R.store, "orders_of", lambda u, n: [{"strategy": "gemini", "symbol": "AAPL"}])
    out = R.user_orders(None, username="ann")["orders"]
    assert out[0]["strategy"] == "gemini", "the operator needs the real name to support a user"
