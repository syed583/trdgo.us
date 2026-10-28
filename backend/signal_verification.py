"""Signal verification (AUDIT ONLY): independently re-derive each model's score and judge the data behind a BUY / SELL.

For every actionable signal this module re-computes, without calling the strategy's own scoring code:

  1. parameters   - the row carries exactly the strategy's configured parameter names, each with its configured weight;
  2. biases       - every bias is on the existing 5-step scale (-1, -.5, 0, .5, 1) or missing;
  3. contributions- points == weight x (0.5 + 0.5 x bias) (missing = 50 % of the weight), to the cent;
  4. total        - score == round(sum of the contributions, 1);
  5. threshold    - the signal equals BUY >= 55 / SELL <= 45 / NEUTRAL (tab_settings.V3_CUTOFFS, unchanged);
  6. data         - per-parameter availability and timestamps (reusing the V2 classification): missing / stale / not-implemented
                    readings are NOT counted as verified market evidence;
  7. entry        - an entry price exists, is positive and sits inside its own stop / target bracket.

It NEVER blocks or changes a trade: ``INTRADAY_VERIFICATION["mode"]`` is "off" or "audit" and nothing else is implemented.
The internal breakdown and the outcome are stored for debugging and audit (table ``intraday_signal_audit``); they are not
served to the frontend. Scoring, weights, formulas and thresholds are read from tab_settings / the strategy tables, never
modified here.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Optional

import tab_settings as cfg

try:                                    # optional: a build without the V2 modules still verifies score integrity and missing evidence
    import intraday_execution as ex
except Exception:  # noqa: BLE001
    ex = None
try:
    import market_calendar as mc
except Exception:  # noqa: BLE001
    mc = None

PASS, FAIL = "PASS", "FAIL"
DATA_OK, DATA_THIN, DATA_UNVERIFIED = "DATA_OK", "DATA_THIN", "DATA_UNVERIFIED"
STEPS = (-1.0, -0.5, 0.0, 0.5, 1.0)


def _session_of(now: datetime) -> dict:
    """{'session', 'trading_date'} for ``now`` (ET). The shared calendar when present, else a plain weekday / clock rule."""
    if mc is not None and hasattr(mc, "session_at"):
        sa = mc.session_at(now)
        return {"session": sa["session"], "trading_date": sa["trading_date"]}
    from datetime import time as _t
    t = now.time()
    sess = ("CLOSED" if now.weekday() >= 5 else "PREMARKET" if _t(4, 0) <= t < _t(9, 30) else "REGULAR" if _t(9, 30) <= t < _t(16, 0)
            else "POSTMARKET" if _t(16, 0) <= t < _t(20, 0) else "CLOSED")
    return {"session": sess, "trading_date": now.date()}


# Per-process counters of the audit's own health, logged as one grep-able line every ``STATS_EVERY_S`` (no database needed, so an audit
# DATABASE failure is still counted):  SIGNAL_AUDIT_STATS {...}
STATS: dict = {"cycles": 0, "signals_seen": 0, "verified": 0, "recorded": 0, "verify_errors": 0, "write_errors": 0, "budget_skips": 0}
STATS_EVERY_S = 600.0
_stats_logged = 0.0


def log_stats(force: bool = False) -> None:
    """One grep-able line every STATS_EVERY_S. The application configures no INFO logging (the root logger is WARNING), so this logger
    owns a stderr handler -- otherwise the line would never reach the service log."""
    import logging
    import sys
    import time
    global _stats_logged
    if force or time.monotonic() - _stats_logged >= STATS_EVERY_S:
        _stats_logged = time.monotonic()
        lg = logging.getLogger("signal_verification.stats")
        if not lg.handlers:
            h = logging.StreamHandler(sys.stderr)
            h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            lg.addHandler(h)
            lg.setLevel(logging.INFO)
            lg.propagate = False
        lg.info("SIGNAL_AUDIT_STATS %s", json.dumps(STATS, sort_keys=True))


_audit_sessionmaker = None


def audit_sessionmaker():
    """A session factory for the audit that uses its OWN least-privileged database role, when ``AUDIT_DATABASE_URL`` is set (the
    role needs only SELECT on demo_trades and INSERT / SELECT on the audit table -- see migrations/..._A_audit_role_TEMPLATE.sql).
    Unset (the default): the audit uses the application's normal session factory. One short connection, never pooled."""
    import os
    global _audit_sessionmaker
    url = (os.getenv("AUDIT_DATABASE_URL") or "").strip()
    if not url:
        return None
    if _audit_sessionmaker is None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import NullPool
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        args = {"prepare_threshold": None} if (url.startswith("postgresql") and ":6543" in url) else {}
        eng = create_engine(url, poolclass=NullPool, connect_args=args)
        _audit_sessionmaker = sessionmaker(bind=eng, autoflush=False)
    return _audit_sessionmaker


# On Supabase every table created in schema public is, by default, reachable through the Data API roles (anon / authenticated). The
# audit table must stay private: enable row-level security (no policy = no access for those roles) and revoke their grants. The
# statements are conditional on the roles existing, so they are harmless on any other PostgreSQL. The application's own role owns the
# table / bypasses RLS and is unaffected.
SECURE_AUDIT_SQL = (
    "ALTER TABLE intraday_signal_audit ENABLE ROW LEVEL SECURITY",
    """DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON TABLE intraday_signal_audit FROM anon;
    REVOKE ALL ON SEQUENCE intraday_signal_audit_id_seq FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON TABLE intraday_signal_audit FROM authenticated;
    REVOKE ALL ON SEQUENCE intraday_signal_audit_id_seq FROM authenticated;
  END IF;
END $$""",
)


def secure_audit_table(engine) -> None:
    """PostgreSQL only: keep the audit table out of the Data API (see SECURE_AUDIT_SQL). Idempotent."""
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text
    with engine.begin() as conn:
        for stmt in SECURE_AUDIT_SQL:
            conn.execute(text(stmt))


def mode() -> str:
    return str((getattr(cfg, "INTRADAY_VERIFICATION", None) or {}).get("mode", "off")).lower()


def enabled() -> bool:
    return mode() == "audit"


def configured_weights(strategy: str) -> dict:
    """The strategy's configured parameter weights, read from the same tables the strategies read (never copied)."""
    if strategy == "grok":
        return dict(cfg.INTRADAY_GROK_WEIGHTS)
    if strategy == "kimi":
        return dict(cfg.INTRADAY_KIMI_WEIGHTS)
    if strategy == "meta":
        return dict(cfg.META_INTRADAY_WEIGHTS)
    if strategy == "gemini":
        return dict(cfg.GEMINI_INTRADAY_WEIGHTS)
    if strategy == "chatgpt":
        import chatgpt_intraday_service as m
        return {k: w for k, _l, w, _s in m.PARAMS}
    if strategy == "claude_pro":
        import claude_pro_intraday_service as m
        return {k: v[1] for k, v in m.BLOCKS.items()}
    return {}


# Parameters that have NO data source in any path of the strategy (declared with a weight, never fed): they are scored neutral by the
# strategy and can never be evidence. Mirrors intraday_execution.REGISTRY (a test keeps the two equal); kept here so a build without the
# V2 modules can still tell "no source exists" from "the source is missing right now".
NO_SOURCE = {"chatgpt": frozenset({"absorption", "breakout_quality", "liquidity_sweeps", "market_structure", "supply_demand", "volatility",
                                   "volume_profile"})}


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def expected_points(weight: float, bias: Optional[float]) -> float:
    """The existing formula: neutral / missing = 50 % of the weight, strong bull = 100 %, strong bear = 0."""
    return weight * 0.5 if bias is None else weight * (0.5 + 0.5 * max(-1.0, min(1.0, float(bias))))


def verify(strategy: str, row: dict, *, now: datetime, session: Optional[str] = None,
           bounds: Optional[dict] = None, prev_close: Optional[datetime] = None) -> dict:
    """Verify one board row. Returns {verdict, integrity, data, checks, reasons, breakdown, recomputed_score, ...}."""
    tol = float((getattr(cfg, "INTRADAY_VERIFICATION", None) or {}).get("score_tolerance", 0.05))
    sess = session or _session_of(now)["session"]
    direction = row.get("signal_raw") or row.get("signal")
    score = row.get("score") if row.get("score") is not None else row.get("score_raw")
    params = row.get("params") or []
    weights = configured_weights(strategy)
    checks: list[dict] = []

    # 1. parameters and weights
    names = [p.get("name") for p in params]
    missing_names = sorted(set(weights) - set(names))
    extra_names = sorted(set(names) - set(weights))
    dup = sorted(n for n, c in Counter(names).items() if c > 1)
    checks.append(_check("parameter set matches the configuration", not (missing_names or extra_names or dup),
                         f"missing={missing_names} extra={extra_names} duplicated={dup}" if (missing_names or extra_names or dup) else ""))
    wrong_w = [(p.get("name"), p.get("max"), weights[p.get("name")]) for p in params
               if p.get("name") in weights and abs(float(p.get("max") or 0) - float(weights[p["name"]])) > 1e-9]
    checks.append(_check("each weight equals the configured weight", not wrong_w, f"{wrong_w}" if wrong_w else ""))

    # 2-3. biases and contributions
    bad_bias, bad_pts, recomputed = [], [], 0.0
    breakdown = []
    for p in params:
        w, b, pts = float(p.get("max") or 0), p.get("bias"), p.get("points")
        if b is not None and not any(abs(float(b) - s) < 1e-9 for s in STEPS):
            bad_bias.append((p.get("name"), b))
        want = expected_points(w, b)
        if pts is None or abs(float(pts) - want) > 0.0051:
            bad_pts.append((p.get("name"), pts, round(want, 2)))
        recomputed += round(want, 2)
        breakdown.append({"name": p.get("name"), "weight": w, "bias": b, "points": pts, "expected_points": round(want, 2),
                          "reading": "missing(neutral 50%)" if b is None else "reading"})
    checks.append(_check("biases are on the 5-step scale", not bad_bias, f"{bad_bias}" if bad_bias else ""))
    checks.append(_check("each contribution = weight x (0.5 + 0.5 x bias)", not bad_pts, f"{bad_pts}" if bad_pts else ""))

    # 4. total
    recomputed_score = round(recomputed, 1)
    total_ok = score is not None and abs(float(score) - recomputed_score) <= tol
    checks.append(_check("final score = sum of the weighted contributions", total_ok,
                         f"reported {score} vs recomputed {recomputed_score}" if not total_ok else ""))
    # also the sum of the *reported* contributions (catches a score that disagrees with its own rows)
    reported_sum = round(sum(float(p.get("points") or 0) for p in params), 1)
    checks.append(_check("final score = sum of the reported contributions", score is not None and abs(float(score) - reported_sum) <= tol,
                         f"reported {score} vs rows {reported_sum}" if score is None or abs(float(score) - reported_sum) > tol else ""))

    # 5. threshold (existing cutoffs, unchanged)
    cut = cfg.V3_CUTOFFS
    want_sig = "BUY" if (score is not None and score >= cut["buy"]) else "SELL" if (score is not None and score <= cut["sell"]) else "NEUTRAL"
    checks.append(_check("signal matches BUY >= 55 / SELL <= 45", direction == want_sig, f"signal {direction} but score {score} -> {want_sig}"
                         if direction != want_sig else ""))

    # 7. entry price and its own bracket
    entry, sl, tp = row.get("entry"), row.get("sl"), row.get("tp")
    if entry is None and isinstance(row.get("raw_levels"), dict):
        entry, sl, tp = (row["raw_levels"].get(k) for k in ("entry", "sl", "tp"))
    entry_ok = entry is not None and float(entry) > 0
    detail = "" if entry_ok else "no usable entry price"
    if entry_ok and sl is not None and tp is not None:
        long = direction == "BUY"
        if not ((float(sl) < float(entry) < float(tp)) if long else (float(tp) < float(entry) < float(sl))):
            entry_ok, detail = False, f"stop {sl} / target {tp} do not bracket entry {entry}"
    if direction in ("BUY", "SELL"):                      # a NEUTRAL row has no entry to verify
        checks.append(_check("entry price present and inside its own stop / target bracket", entry_ok, detail))

    integrity = all(c["ok"] for c in checks)

    # 6. data (separate from integrity: a clean score on thin data is NOT verified evidence)
    data: dict = {"status": DATA_UNVERIFIED, "staleness": "NOT ASSESSED", "reasons": []}
    meta = row.get("signal_meta")
    nosrc = NO_SOURCE.get(strategy, frozenset())
    covered = sum(float(p.get("max") or 0) for p in params if p.get("bias") is not None)
    total_w = sum(float(p.get("max") or 0) for p in params if p.get("name") not in nosrc)      # eligible weight (a source exists)
    basic = {"eligible_coverage_pct": round(100 * covered / total_w, 1) if total_w else 0.0,
             "missing_weight_pct": round(100 * (total_w - covered) / total_w, 1) if total_w else 0.0,
             "no_source_weight": sum(float(p.get("max") or 0) for p in params if p.get("name") in nosrc)}
    if direction in ("BUY", "SELL") and (ex is None or not meta):
        # No per-signal timestamps on this row (older build, or timestamps not enabled): staleness cannot be judged. Missing evidence still can.
        thin = basic["eligible_coverage_pct"] < float(getattr(cfg, "MIN_COVERAGE_PCT", 70.0))
        data = dict(basic, staleness="NOT ASSESSED", status=DATA_THIN if thin else DATA_UNVERIFIED,
                    reasons=([f"only {basic['eligible_coverage_pct']:.0f}% of the eligible weight has a reading (missing readings are scored "
                              f"neutral, not verified)"] if thin else []) + ["no per-signal timestamps: staleness cannot be judged"])
    elif direction in ("BUY", "SELL"):
        try:
            if bounds is None:
                bounds = mc.session_bounds(_session_of(now)["trading_date"])
            if prev_close is None:
                import intraday_workflow as wf
                prev_close = wf.last_close_before(now)
            esess = sess if sess in (ex.PREMARKET, ex.REGULAR) else ex.REGULAR
            eng_age = (row.get("data") or {}).get("signals_age_s")
            engine_ok = True if ex.data_path(esess) != "live" else (
                eng_age is not None and float(eng_age) <= float(cfg.INTRADAY_FRESHNESS["engine_score"][1]))
            ed = ex.evaluate_data(strategy, direction, sess if sess in (ex.PREMARKET, ex.REGULAR) else ex.REGULAR,
                                  dict(row, signal_meta=meta, params=params), now=now, bounds=bounds, prev_close=prev_close,
                                  engine_ok=engine_ok)
            cov = ed["coverage"]
            has_ts = bool(meta)
            status = DATA_OK if ed["ok"] else DATA_THIN
            if not has_ts:
                status = DATA_UNVERIFIED
            data = {**basic, "staleness": "ASSESSED", "stale_parameters": [r["name"] for r in ed["classified"] if r["category"] == ex.STALE],
                    "status": status, "reasons": ed["reasons"] + ([] if has_ts else ["no per-signal timestamps on this row"]),
                    "code": ed["code"], "eligible_coverage_pct": cov["eligible_pct"], "total_coverage_pct": cov["total_pct"],
                    "verified_weight": cov["available_weight"], "by_category": cov["by_category"], "agreement": ed["agreement"],
                    "price_age_s": (row.get("data") or {}).get("price_age_s"), "engine_age_s": (row.get("data") or {}).get("signals_age_s")}
        except Exception as exc:  # noqa: BLE001 - an audit problem is reported, never raised into the tracker
            data = {"status": DATA_UNVERIFIED, "reasons": [f"data check could not run: {type(exc).__name__}"]}
    reasons = [f"{c['check']}: {c['detail']}" if c["detail"] else c["check"] for c in checks if not c["ok"]]
    return {"strategy": strategy, "direction": direction, "score": score, "recomputed_score": recomputed_score,
            "score_diff": None if score is None else round(float(score) - recomputed_score, 2), "entry_price": entry,
            "verdict": PASS if integrity else FAIL, "integrity": integrity, "data": data,
            "data_status": data["status"], "checks": checks, "reasons": reasons, "breakdown": breakdown, "session": sess}


def attribution(strategy: str, symbol: str, direction: str, entry, score, trade) -> dict:
    """Is the paper trade the tracker opened really THIS strategy's, THIS symbol's, THIS side's signal at THIS entry price and score?
    ``trade`` is the committed demo_trades row (strategy, symbol, side, entry_price, score_at_entry)."""
    problems = []
    if trade is None:
        return {"ok": None, "detail": "no trade row found for the opened id"}
    if trade.strategy != strategy:
        problems.append(f"trade is attributed to {trade.strategy}, signal is {strategy}")
    if trade.symbol != symbol:
        problems.append(f"trade symbol {trade.symbol} != signal symbol {symbol}")
    if trade.side != ("LONG" if direction == "BUY" else "SHORT"):
        problems.append(f"trade side {trade.side} does not match the {direction} signal")
    if entry is not None and trade.entry_price is not None and abs(float(trade.entry_price) - float(entry)) > 1e-6:
        problems.append(f"trade entry {trade.entry_price} != signal entry {entry}")
    tol = float((getattr(cfg, "INTRADAY_VERIFICATION", None) or {}).get("score_tolerance", 0.05))
    if score is not None and trade.score_at_entry is not None and abs(float(trade.score_at_entry) - float(score)) > tol:
        problems.append(f"trade score {trade.score_at_entry} != signal score {score}")
    return {"ok": not problems, "detail": "; ".join(problems)}


# ----------------------------------------------------------------------------------------------------- recording ------
_last: dict = {}


def record(s, v: dict, *, now: datetime, symbol: str, opened_id: Optional[int], held: bool, attr: Optional[dict] = None) -> bool:
    """Store the outcome when it changed for this strategy / symbol / session / direction. The caller owns the transaction."""
    from models_demo_trades import IntradaySignalAudit
    sess = _session_of(now)
    key = (v["strategy"], symbol, str(sess["trading_date"]), v["session"], v["direction"])
    attr = attr or {"ok": None, "detail": None}
    sig = (v["verdict"], v["data_status"], v["score"], opened_id is not None, held, attr["ok"])
    if _last.get(key) == sig:
        return False
    _last[key] = sig
    s.add(IntradaySignalAudit(
        ts=now, strategy=v["strategy"], symbol=symbol, trading_date=sess["trading_date"], session_type=v["session"], direction=v["direction"],
        score=v["score"], recomputed_score=v["recomputed_score"], score_diff=v["score_diff"], entry_price=v["entry_price"],
        verdict=v["verdict"], data_status=v["data_status"], failed_checks=json.dumps(v["reasons"]),
        checks=json.dumps(v["checks"], default=str), breakdown=json.dumps(v["breakdown"], default=str),
        data=json.dumps(v["data"], default=str), legacy_opened=opened_id is not None, trade_id=opened_id, legacy_held=held,
        attribution_ok=attr["ok"], attribution_detail=attr["detail"] or None))
    return True


# ------------------------------------------------------------------------------------------------------- report -------
def report(session_factory, day_from: date, day_to: Optional[date] = None) -> dict:
    """How many logged signals pass or fail, and why. Reads only."""
    from models_demo_trades import IntradaySignalAudit as A
    day_to = day_to or day_from
    s = session_factory()
    try:
        rows = s.query(A).filter(A.trading_date >= day_from, A.trading_date <= day_to).order_by(A.id).all()
        last: dict = {}
        for r in rows:
            last[(r.strategy, r.symbol, str(r.trading_date), r.session_type, r.direction)] = r
        per: dict = defaultdict(lambda: {"signals": 0, "integrity_pass": 0, "integrity_fail": 0, "data_ok": 0, "data_thin": 0,
                                         "data_unverified": 0, "stale_not_assessed": 0, "stale_assessed_with_stale_inputs": 0,
                                         "opened_by_legacy": 0, "already_held": 0, "attribution_ok": 0, "attribution_mismatch": 0,
                                         "fail_reasons": Counter(), "data_reasons": Counter()})
        for (strat, *_), r in last.items():
            p = per[strat]
            p["signals"] += 1
            p["integrity_pass" if r.verdict == PASS else "integrity_fail"] += 1
            p[{DATA_OK: "data_ok", DATA_THIN: "data_thin"}.get(r.data_status, "data_unverified")] += 1
            p["opened_by_legacy"] += 1 if r.legacy_opened else 0
            p["already_held"] += 1 if r.legacy_held else 0
            if r.attribution_ok is True:
                p["attribution_ok"] += 1
            elif r.attribution_ok is False:
                p["attribution_mismatch"] += 1
            dj = json.loads(r.data or "{}")
            if dj.get("staleness") == "ASSESSED":
                p["stale_assessed_with_stale_inputs"] += 1 if dj.get("stale_parameters") else 0
            else:
                p["stale_not_assessed"] += 1
            for why in json.loads(r.failed_checks or "[]"):
                p["fail_reasons"][why.split(":")[0]] += 1
            if r.data_status != DATA_OK:
                for why in (json.loads(r.data or "{}").get("reasons") or [])[:2]:
                    p["data_reasons"][why[:70]] += 1
        out = {k: {**{a: b for a, b in v.items() if not isinstance(b, Counter)}, "fail_reasons": dict(v["fail_reasons"]),
                   "data_reasons": dict(v["data_reasons"].most_common(5))} for k, v in sorted(per.items())}
        tot = Counter()
        for v in out.values():
            for k in ("signals", "integrity_pass", "integrity_fail", "data_ok", "data_thin", "data_unverified", "stale_not_assessed",
                      "stale_assessed_with_stale_inputs", "opened_by_legacy", "already_held", "attribution_ok", "attribution_mismatch"):
                tot[k] += v[k]
        return {"from": str(day_from), "to": str(day_to), "total": dict(tot), "per_strategy": out,
                "note": "audit only: nothing here blocked or changed a trade"}
    finally:
        s.close()


# ------------------------------------------------------------------------------- existing paper trades (read-only) -----
def verify_trade(t) -> dict:
    """What can be re-checked about an EXISTING paper trade from what the book stored: the entry score against the unchanged
    thresholds and the trade's own side, and the entry / stop / target bracket. (Per-parameter contributions were not stored
    before the audit existed, so the score cannot be re-derived for history; the audit table keeps them from now on.)"""
    cut = cfg.V3_CUTOFFS
    reasons = []
    if t.score_at_entry is None:
        reasons.append("no entry score stored")
    elif t.side == "LONG" and t.score_at_entry < cut["buy"]:
        reasons.append(f"LONG opened at score {t.score_at_entry} (< BUY {cut['buy']})")
    elif t.side == "SHORT" and t.score_at_entry > cut["sell"]:
        reasons.append(f"SHORT opened at score {t.score_at_entry} (> SELL {cut['sell']})")
    if not t.entry_price or t.entry_price <= 0:
        reasons.append("no usable entry price")
    elif t.stop is not None and t.target is not None:
        ok = (t.stop < t.entry_price < t.target) if t.side == "LONG" else (t.target < t.entry_price < t.stop)
        if not ok:
            reasons.append(f"stop {t.stop} / target {t.target} do not bracket entry {t.entry_price}")
    else:
        reasons.append("stop or target missing")
    if t.shares != 100:
        reasons.append(f"size {t.shares} (convention is 100)")
    return {"ok": not reasons, "reasons": reasons}


def history_report(session_factory, day_from: date, day_to: Optional[date] = None) -> dict:
    """READ-ONLY. How many existing paper trades would pass or fail the re-checks that history allows, and why."""
    from models_demo_trades import DemoTrade
    day_to = day_to or day_from
    s = session_factory()
    try:
        rows = s.query(DemoTrade).filter(DemoTrade.session_date >= day_from, DemoTrade.session_date <= day_to).all()
        per: dict = defaultdict(lambda: {"trades": 0, "pass": 0, "fail": 0, "reasons": Counter()})
        for t in rows:
            v = verify_trade(t)
            p = per[t.strategy]
            p["trades"] += 1
            p["pass" if v["ok"] else "fail"] += 1
            for r in v["reasons"]:
                p["reasons"][r.split(" (")[0].split(" opened")[0] if "opened at" in r else r.split(" stop ")[0][:60]] += 1
        out = {k: {**{a: b for a, b in v.items() if a != "reasons"}, "reasons": dict(v["reasons"].most_common(6))} for k, v in sorted(per.items())}
        return {"from": str(day_from), "to": str(day_to), "trades": len(rows), "pass": sum(v["pass"] for v in out.values()),
                "fail": sum(v["fail"] for v in out.values()), "per_strategy": out,
                "limits": "entry score vs thresholds, side, entry/stop/target bracket and size only; per-parameter contributions were "
                          "not stored for history", "note": "read-only; nothing was changed"}
    finally:
        s.close()
