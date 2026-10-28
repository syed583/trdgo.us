"""LARGER CAPPED PROVIDER SAMPLE for the earnings outcome grader (written locally, NOT run; needs approval of the request count).

What one run does
  * picks up to --max-requests symbols (hard ceiling 150) so that completed Before Open and After Close events are
    balanced, then fetches each symbol's daily candles ONCE (GET /api/stock/{symbol}/ohlc/1d, 90 rows), sequential,
    2 s apart, background priority, stops at the first non-OK answer other than NO_DATA;
  * for every completed event of those symbols it computes the outcome from REGULAR-session closes only
    (market_time 'r') for both the baseline and the reaction session, and checks the data: completed sessions,
    trading-day dates, missing sessions, split-like jumps, thin (partial-looking) volume;
  * compares each verified outcome with the stored legacy move and gives the reason for every difference;
  * recalculates PROVISIONAL BUY / SELL / NEUTRAL accuracy for the scored models using only eligible pre-release
    snapshots and the verified outcomes (a SAMPLE: events whose symbol was not fetched stay pending);
  * describes how any share-class symbol (a dot in the ticker) was answered, without guessing another ticker.
  * Database: SELECT-only in a READ ONLY transaction. Output: --out JSON only. Nothing is written or changed.
Usage (backend dir, production venv): python <path>\\outcome_sample.py --max-requests 100 --out <file>.json
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import sys
import time
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_EMBEDDED = {
    "market_calendar": (
        "H4sIAAAAAAAC/6VYe2/bRhL/n59ioKI4sqEUS7KTs68qznXVXFLHKfy4wFcclBW5khamuLklaUcp8t3vN7skRUqM48MZSETu"
        "vJ87w16v591ckfxPofINrYW5kzlFIpFpLMwJ5UbEKl1SLDZZSCudKPckhUk2FCU6kxmJNCb8Zkqn7ohytZbZwPNuMhlTrimW"
        "kYolvf/H9ALYTJ2Ca0ZGiii3ZDidS1pCnIxPSNDPcqGN7L/7KFNgfdQmd8gZqZTylfQqHn0oVEkPmfnpIpemf2b1KCkdCaXy"
        "U073AjY0CWKcK4BNWwlvbjUAochr61YC1kZ5IZLK+pj84fjk4ICm16RT55e+c4Lz1PBFBWUZDyqTARzzewHWiyK11gMr1dAi"
        "f9DmbkCnScK8ozsSBoqtZa4iWmo4Ol8ZXSxXdLqWRkXi+YV8mN2ChjLN0hK1XEFXcQ+/eBwCUhlUTuMEas431gefNaxNF4yf"
        "i7nIJGTLexgPuKCF+gTUm+sz0otFJnNoelkkkLzQSaIfLIeL26tplQmURSsZA+OEoAvdwvy/ZPQLAL6gK5EXhpEqEKtz8Q6e"
        "mGfS3EOQdqH8aOS90kVGvxpmGoT0VpgcQTsvbFx+4wx8YwbMOKT3IlvhINcpRP2sTL6K+fiV1nHJAPRyrY0SiaXw3hTwrZRp"
        "vgrpdRpL5FQs00g6fudiro17vIaz7rKlYgfazDhbGZXla0Tdt+GrK8ClxQJ5wEYI70HKO3BFxOSuefAvTrOcGIfNG9C0WT11"
        "/gQnFp09JjiHW+qE3laX6b202r0pwGVMDytpBW2cPm81SnfTv14VJsPDgG7SKkqxlYnUy6yiCfjhTKXe1e/Ts9en57Oz83dX"
        "N5fTq4HXQ1/wFkavaTZbFAiknM1IrW05iTTVubCZW+IgmaRLOIfB72F9GtL2/1gmuXCPnIqO3BaC1klW0SemmEUCWjt4nbQl"
        "+F94f413z7u4pUn96vd2C6MXeJfTVzfnp5fWtCmQWbI/fBHSQeBNTy/Pb3cgYwvxvqOzylWpnusY4dJFEtcJjwSPY0JKyNr/"
        "JD9FiNhSWgfBJNBCM5T7rntPKFZR/ofzUpabf0P6nx7hj4/80cHoCI0jpGOkRO/CetrlMsoS8S1s5yP/zYDOUCjSBL3Q++J5"
        "XiwXNEOaz8pU8zeSm7hK85DWGoDyuQSXb6n9Daj/kxV/4hSBSlYZZlFSQ6egBD6bbKPpc0FM/JIp9SkeVPKDgL6nl47ISGRR"
        "yrQNUkbMJimIwLo0IBFZ/r9Y8JjmEOZbOppMaDhCaylfv+c3QIdsFMTvWDPc0XkPwW8YCWhV287c0hAJQ6TZmrCjKErsvWSM"
        "FA2Bf+mq4NJFaqU63ay5I74ycsmdDD0m4QdcB8GAa5MZoI7mIUWw15oKk45D9/j8OQ0PDsL6/ODAuQYXN7DnDD8EMUCHFrDA"
        "qT+HO/4aMGx0ZE+X7rQP8DP2EiBjC1gxYHhMP+DKeEaMwR5aMtYRu2DsxKmQ7oAZleKiWlzC9OMR8EfgIctfBR4r/LuzXrSI"
        "a0ZkGcMhEFaMyJiJ1eXwaOiQXHzJZ3gCBi+BsrZEh07pYVlbG8bqRoPSQ5sPrbjvFQDHuApv1ed9DCyMuR/e+gYEoLqayhjj"
        "xDXqOppq0Swcztcjx+jxRCwzdZ/6RQf1s2/kOUz7e917/bX4lKnPcvLiMLAWV5ffbka3e1lt/Vs3THZcmbjaPzCPD7Xxusg7"
        "m+IXC0037W40rDsRzE43u3ZT5993e67/23ZEUdzm8/rqrj0Hvf6A9A6/sXq99tDjTJHJrlI/NiPpOH6VmsF7LdxajHIeO6lf"
        "n44e5TFq8uicohrkjd7V0R5Hjklj5mqQ7vXvkI74UnW6NyazOu9tm/ppQrj4Rm1PNYpsG35c3sPjwPHbznYNDbqIXoZ0WNLs"
        "zoCPeu3Yeq2Mdz0rPh4sRGvM4ixNa6hsk3bpOUSYRkelptuZryYsSxX0ZR9S2azc0masQrMZzTFTnXS1B/j6iGAJD3+0N53s"
        "9o1fRZLJ9n3IxQLSuiPEA5sppUq8aH1VqVaHvMbYtFAG03Fj0+TqRytIqjn4Q7xtFF2jR9kLHlYKc5nVbMclwdakR+gbTZCN"
        "4K3k6UZw0nfbUO6RbSP6/6cR/ScaodOZNrMnxqOOrtrPqgB9LZMdkQ1akp7otCdI2ucUbBPertkzu0B9PeG/4cS95M6bhTpp"
        "D9IuvxulXVcVXzktyo67Yk/otSlkV13+SId2s/MhsDG22rN4YAcYdEmMK6jdJsrLNsY4eERky27n0fLzRodDqxWuleuYSYtE"
        "mH77mw9Wk2b+I99D4s8lGF0feNvc7oip+yLAHzsaw8+3wiUUhPxTJIWcGqONv+j9GX+pLm7RlN0Ldoc4ljuI9HquUtgXUnPx"
        "U/sZVaZga3XEzvqZd9DJxW3Q5bZZkUePuK5UZsfTwQBto1yF/ephAE6B91/l37o1mRMAAA=="),
    "earnings_outcomes": (
        "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"),
    "earnings_scoreboard": (
        "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"
        "TJ3kRpknvSyTOWzsikzvhM9QbONfRS1cCDI7AAA="),
}

HARD_CEILING = 150
DEFAULT_REQUESTS = 100
PAUSE_SECONDS = 2.0
JUMP_PCT = 35.0                 # day-over-day close change that is flagged (possible split or bad bar)
THIN_VOLUME = 0.25              # regular-row volume under this share of the neighbouring median is flagged
MODELS = {"claude_new": "Claude", "claude_pro": "Claude Pro", "gemini": "Gemini", "grok": "Grok",
          "chatgpt": "ChatGPT", "meta": "Meta", "kimi": "Kimi", "ped": "Pre-Earnings Direction"}
SQL_SNAPS = ("SELECT model, symbol, earnings_date, report_timing, snapshot_date, predicted_at, evaluated_at, "
             "actual_move_pct, signal, score FROM model_predictions WHERE earnings_date IS NOT NULL")


def load_embedded():
    for name in ("market_calendar", "earnings_outcomes", "earnings_scoreboard"):
        src = gzip.decompress(base64.b64decode(_EMBEDDED[name])).decode("utf-8")
        mod = types.ModuleType(name)
        mod.__file__ = f"<embedded {name}>"
        sys.modules[name] = mod
        exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    eo, sb = sys.modules["earnings_outcomes"], sys.modules["earnings_scoreboard"]

    def _blocked(name):
        def f(*a, **k):
            raise RuntimeError(f"{name} is disabled in the sample tool")
        return f
    for fn in ("run", "ensure_table", "load_outcomes", "_save", "sync_legacy_columns", "events_to_review",
               "default_bars_fetcher"):
        setattr(eo, fn, _blocked("earnings_outcomes." + fn))
    for fn in ("scoreboard", "load_rows"):
        setattr(sb, fn, _blocked("earnings_scoreboard." + fn))
    return sys.modules["market_calendar"], eo, sb


def _run_ro(sql):
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        return [dict(r._mapping) for r in db.execute(text(sql))]
    finally:
        db.rollback()
        db.close()


def _d(v):
    if v is None or (isinstance(v, date) and not isinstance(v, datetime)):
        return v
    return v.date() if isinstance(v, datetime) else date.fromisoformat(str(v)[:10])


def _utc(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------------------------------------ events and selection
def completed_events(snaps, now):
    """Completed, timed events of the scored models with their stored legacy value and model eligibility."""
    from zoneinfo import ZoneInfo
    mc, eo, sb = load_embedded()
    today_et = now.astimezone(ZoneInfo("America/New_York")).date()
    ev = {}
    for r in snaps:
        if r["model"] not in MODELS or not r["report_timing"]:
            continue
        key = (r["symbol"], _d(r["earnings_date"]))
        e = ev.setdefault(key, {"label": r["report_timing"], "legacy_move": None, "stamped": False, "eligible": False})
        if r["actual_move_pct"] is not None and e["legacy_move"] is None:
            e["legacy_move"] = float(r["actual_move_pct"])
        if r["evaluated_at"] is not None:
            e["stamped"] = True
        if r["model"] == "claude_new" and sb.classify(_d(r["earnings_date"]), r["report_timing"], _utc(r["predicted_at"])) == "BEFORE_RELEASE":
            e["eligible"] = True
    out = []
    for (sym, ed), e in ev.items():
        kind = eo.timing_kind(e["label"])
        if kind is None or ed > today_et:
            continue
        base_d, react_d = eo.reaction_window(ed, kind)
        if now < eo.ready_at(react_d):
            continue
        out.append({"symbol": sym, "earnings_date": ed, "kind": kind, "legacy_move": e["legacy_move"], "stamped": e["stamped"],
                    "eligible": e["eligible"], "base": base_d, "react": react_d})
    return sorted(out, key=lambda x: (x["earnings_date"], x["symbol"]))


def pick_symbols(events, n):
    """Up to n distinct symbols: share-class symbols first (at most 3, to study them), then eligible After Close and
    Before Open events in alternation (deterministic), then the rest."""
    n = max(0, min(int(n), HARD_CEILING))
    chosen, seen = [], set()

    def take(e):
        if e["symbol"] not in seen and len(chosen) < n:
            seen.add(e["symbol"])
            chosen.append(e["symbol"])
    for e in [x for x in events if not x["symbol"].isalpha()][:3]:
        take(e)
    amc = [e for e in events if e["kind"] == "AMC" and e["eligible"]]
    bmo = [e for e in events if e["kind"] == "BMO" and e["eligible"]]
    for i in range(max(len(amc), len(bmo))):
        for lst in (amc, bmo):
            if i < len(lst):
                take(lst[i])
    for e in events:
        take(e)
    return chosen


# --------------------------------------------------------------------------------------------------- reading the rows
def regular_closes(rows):
    """date -> regular-session close ('r' rows only; unlabelled single-bar dates use the highest-volume row)."""
    by_date = {}
    for r in rows or []:
        raw_day = r.get("date") or r.get("start_time")
        try:
            d = date.fromisoformat(str(raw_day)[:10])
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        by_date.setdefault(d, []).append((r, c))
    out, fallback = {}, set()
    for d, items in by_date.items():
        reg = [c for r, c in items if str(r.get("market_time") or "").strip().lower() == "r"]
        if reg:
            out[d] = reg[-1]
        elif all(not r.get("market_time") for r, _ in items):
            def vol(r):
                for k in ("total_volume", "volume"):
                    try:
                        return float(r.get(k))
                    except (TypeError, ValueError):
                        continue
                return 0.0
            out[d] = max(items, key=lambda x: vol(x[0]))[1]
            fallback.add(d)
    return out, fallback


def last_row_closes(rows):
    out = {}
    for r in rows:
        try:
            out[date.fromisoformat(str(r.get("date"))[:10])] = float(r["close"])
        except (TypeError, ValueError, KeyError):
            continue
    return out


def legacy_move(rows, earnings_date, kind):
    bars = [b for b in rows if b.get("date") and b.get("close") not in (None, "")]
    bars.sort(key=lambda b: b["date"])
    closes = [(str(b["date"])[:10], float(b["close"])) for b in bars]
    idx = next((i for i, (bd, _) in enumerate(closes) if bd >= earnings_date.isoformat()), None)
    if idx is None or idx == 0:
        return None
    if kind == "AMC":
        if idx + 1 >= len(closes):
            return None
        base, react = closes[idx][1], closes[idx + 1][1]
    else:
        base, react = closes[idx - 1][1], closes[idx][1]
    return round((react - base) / base * 100, 2) if base else None


def quality(rows, closes, now):
    """Checks on one symbol's series: trading-day dates, missing sessions, split-like jumps, thin volume, today's session."""
    from zoneinfo import ZoneInfo
    mc, eo, sb = load_embedded()
    flags = {"regular_rows_on_non_trading_days": [], "missing_regular_sessions": [], "jumps": [], "possible_split": [],
             "thin_volume_dates": [], "latest_session_not_complete": None}
    if not closes:
        return flags
    days = sorted(closes)
    flags["regular_rows_on_non_trading_days"] = [str(d) for d in days if not mc.is_trading_day(d)]
    d = days[0]
    while d <= days[-1]:
        if mc.is_trading_day(d) and d not in closes:
            flags["missing_regular_sessions"].append(str(d))
        d += timedelta(days=1)
    for a, b in zip(days, days[1:]):
        if closes[a]:
            chg = (closes[b] - closes[a]) / closes[a] * 100
            if abs(chg) >= JUMP_PCT:
                flags["jumps"].append({"from": str(a), "to": str(b), "pct": round(chg, 1)})
            ratio = closes[b] / closes[a]
            for k in (2, 3, 4, 5, 10):
                if abs(ratio - k) / k < 0.03 or abs(ratio - 1 / k) * k < 0.03:
                    flags["possible_split"].append({"from": str(a), "to": str(b), "ratio": round(ratio, 3)})
    vols = {}
    for r in rows:
        if str(r.get("market_time") or "").lower() == "r":
            try:
                vols[date.fromisoformat(str(r["date"])[:10])] = float(r.get("volume") or r.get("total_volume") or 0)
            except (TypeError, ValueError):
                pass
    vs = sorted(vols)
    for i, dd in enumerate(vs):
        neigh = sorted(vols[x] for x in vs[max(0, i - 3):i] + vs[i + 1:i + 4])
        if neigh and vols[dd] < THIN_VOLUME * neigh[len(neigh) // 2]:
            flags["thin_volume_dates"].append(str(dd))
    today_et = now.astimezone(ZoneInfo("America/New_York")).date()
    last = days[-1]
    if last == today_et and mc.is_trading_day(last) and now < mc.session_close_utc(last) + timedelta(minutes=45):
        flags["latest_session_not_complete"] = str(last)
    return flags


def shape_of(rows):
    per_date, labels = {}, {}
    for r in rows:
        k = str(r.get("date") or r.get("start_time") or "")[:10]
        per_date[k] = per_date.get(k, 0) + 1
        labels[str(r.get("market_time"))] = labels.get(str(r.get("market_time")), 0) + 1
    hist = {}
    for v in per_date.values():
        hist[v] = hist.get(v, 0) + 1
    return {"rows": len(rows), "distinct_dates": len(per_date), "dates_with_n_rows": dict(sorted(hist.items())), "market_time_labels": labels}


def band(m):
    return None if m is None else ("BUY" if m > 0.5 else "SELL" if m < -0.5 else "NEUTRAL")


def analyse(events, fetched, snaps, now=None):
    mc, eo, sb = load_embedded()
    now = now or datetime.now(timezone.utc)
    per_symbol, evrecs, outcomes = {}, [], {}
    by_symbol = {}
    for e in events:
        by_symbol.setdefault(e["symbol"], []).append(e)
    for sym, rows in fetched.items():
        if not isinstance(rows, list):
            per_symbol[sym] = {"provider": rows}
            continue
        closes, fb = regular_closes(rows)
        fl = quality(rows, closes, now)
        per_symbol[sym] = {"shape": shape_of(rows), "regular_dates": len(closes), "fallback_dates": sorted(str(d) for d in fb), "flags": fl,
                           "first_regular_date": str(min(closes)) if closes else None, "last_regular_date": str(max(closes)) if closes else None}
        last_rows = last_row_closes(rows)
        for e in by_symbol.get(sym, []):
            o = eo.evaluate(sym, e["earnings_date"], e["kind"], now, closes)
            cur = eo.evaluate(sym, e["earnings_date"], e["kind"], now, last_rows)
            rep = legacy_move(rows, e["earnings_date"], e["kind"])
            touched = {str(e["base"]), str(e["react"])}
            bad_dates = ({x["from"] for x in fl["jumps"]} | {x["to"] for x in fl["jumps"]} |
                         {x["from"] for x in fl["possible_split"]} | {x["to"] for x in fl["possible_split"]} |
                         set(fl["thin_volume_dates"]) | set(fl["regular_rows_on_non_trading_days"]) | {str(d) for d in fb})
            flagged = bool(touched & bad_dates)
            rec = {"symbol": sym, "earnings_date": str(e["earnings_date"]), "kind": e["kind"], "eligible_claude": e["eligible"],
                   "base_date": str(e["base"]), "reaction_date": str(e["react"]), "status": o["status"], "stored_legacy": e["legacy_move"],
                   "legacy_logic_replica": rep, "current_fetcher_move": cur["move_pct"], "verified_move": o["move_pct"],
                   "base_close": o["base_close"], "reaction_close": o["reaction_close"], "data_flag": flagged}
            if o["status"] == eo.GRADED:
                rec["verified"] = not flagged
                sv = e["legacy_move"]
                if sv is None:
                    rec["vs_stored"] = "no stored legacy value"
                elif abs(sv - o["move_pct"]) <= 0.05:
                    rec["vs_stored"] = "match"
                elif rep is not None and abs(rep - sv) <= 0.011:
                    rec["vs_stored"] = "legacy row-selection error (replica reproduces the stored value)"
                else:
                    rec["vs_stored"] = "other: stored value not reproduced (stamped before the bars existed, or a data revision)"
                rec["class_change"] = (sv is not None and band(sv) != band(o["move_pct"]))
                rec["current_fetcher_equals_verified"] = cur["move_pct"] == o["move_pct"]
                if not flagged:
                    outcomes[(sym, e["earnings_date"])] = {"status": eo.GRADED, "move_pct": o["move_pct"], "direction": o["direction"], "timing": e["kind"]}
            evrecs.append(rec)
    return per_symbol, evrecs, outcomes


def summarise(evrecs):
    g = [r for r in evrecs if r["status"] == "GRADED"]
    s = {"events_in_fetched_symbols": len(evrecs), "graded": len(g), "verified_unflagged": sum(1 for r in g if r["verified"]),
         "pending_price": sum(1 for r in evrecs if r["status"] != "GRADED"), "by_timing": {}, "differences": {}}
    for kind in ("BMO", "AMC"):
        k = [r for r in g if r["kind"] == kind]
        s["by_timing"][kind] = {"graded": len(k), "with_stored_value": sum(1 for r in k if r["stored_legacy"] is not None),
                                "stored_matches": sum(1 for r in k if r["vs_stored"] == "match"),
                                "stored_differs": sum(1 for r in k if r["vs_stored"].startswith(("legacy", "other"))),
                                "class_changes": sum(1 for r in k if r["class_change"]),
                                "no_stored_value": sum(1 for r in k if r["vs_stored"] == "no stored legacy value")}
    for r in g:
        s["differences"][r["vs_stored"]] = s["differences"].get(r["vs_stored"], 0) + 1
    s["current_fetcher_equals_verified"] = [sum(1 for r in g if r["current_fetcher_equals_verified"]), len(g)]
    return s


def accuracy(snaps, outcomes, now):
    mc, eo, sb = load_embedded()
    rows = [{"model": r["model"], "symbol": r["symbol"], "earnings_date": _d(r["earnings_date"]), "report_timing": r["report_timing"],
             "predicted_at": _utc(r["predicted_at"]), "signal": r["signal"], "score": r["score"]}
            for r in snaps if r["model"] in MODELS]
    res = sb.build(rows, outcomes, MODELS, now=now)
    out = {}
    for m in res["models"]:
        out[m["model"]] = {"label": m["label"], "eligible": m["events_eligible"], "graded": m["events_graded"], "pending": m["events_pending"],
                           "missing_outcome": m["events_missing_outcome"],
                           "BUY": [m["buy"]["graded"], m["buy"]["correct"], m["buy"]["ci95"]],
                           "SELL": [m["sell"]["graded"], m["sell"]["correct"], m["sell"]["ci95"]],
                           "NEUTRAL_calls": m["neutral"]["graded"], "baseline": m["baseline"]}
    return out


# ----------------------------------------------------------------------------------------------------- provider access
def fetch_all(symbols, uw, pause=PAUSE_SECONDS, cap=DEFAULT_REQUESTS, sleep=time.sleep):
    cap = min(cap, HARD_CEILING)
    fetched, made = {}, 0
    try:
        prev = uw.is_background()
        uw.set_background(True)
    except Exception:  # noqa: BLE001
        prev = None
    try:
        for sym in symbols:
            if made >= cap:
                fetched[sym] = "skipped: request cap reached"
                continue
            made += 1
            out = uw.candles(sym, "1d", limit=90)
            st = out.get("status")
            if st == "OK":
                fetched[sym] = list(uw._rows(out))
            elif st == "NO_DATA":
                fetched[sym] = f"NO_DATA ({out.get('detail') or 'no candles for this symbol string'})"
            else:
                fetched[sym] = f"stopped: provider answered {st}"
                for rest in symbols[symbols.index(sym) + 1:]:
                    fetched[rest] = "skipped: stopped after a non-OK answer"
                break
            sleep(pause)
    finally:
        if prev is not None:
            try:
                uw.set_background(prev)
            except Exception:  # noqa: BLE001
                pass
    return fetched, made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-requests", type=int, default=DEFAULT_REQUESTS)
    a = ap.parse_args()
    n = max(1, min(a.max_requests, HARD_CEILING))
    if os.path.exists(os.path.join(os.getcwd(), "database.py")) and os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    import database  # noqa: F401  (loads backend/.env)
    if (os.environ.get("APP_ENV") or "").strip().lower() != "production":
        sys.exit("APP_ENV is not 'production': refusing.")
    now = datetime.now(timezone.utc)
    snaps = _run_ro(SQL_SNAPS)
    events = completed_events(snaps, now)
    symbols = pick_symbols(events, n)
    print(f"completed timed events: {len(events)} (BMO {sum(1 for e in events if e['kind'] == 'BMO')}, AMC {sum(1 for e in events if e['kind'] == 'AMC')}) | "
          f"symbols planned: {len(symbols)} (limit {n}, hard ceiling {HARD_CEILING})")
    import unusualwhales_service as uw
    fetched, made = fetch_all(symbols, uw, cap=n)
    per_symbol, evrecs, outcomes = analyse(events, fetched, snaps, now)
    summary = summarise(evrecs)
    acc = accuracy(snaps, outcomes, now)
    res = {"generated_at": now.isoformat(timespec="seconds"), "provider_requests_made": made, "symbols": per_symbol,
           "events": evrecs, "summary": summary, "provisional_accuracy_sample": acc}
    Path(a.out).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print("wrote", a.out, "| provider requests made:", made)
    print("\nSUMMARY:", json.dumps(summary))
    share = {s: v for s, v in per_symbol.items() if not s.isalpha()}
    print("\nSHARE-CLASS SYMBOLS (answer as received; no replacement ticker was tried):")
    for s, v in share.items():
        print(" ", s, json.dumps(v if "provider" in v else {"shape": v["shape"], "regular_dates": v["regular_dates"], "last_regular_date": v["last_regular_date"]}, default=str))
    bad = {s: v["flags"] for s, v in per_symbol.items() if "flags" in v and any(v["flags"][k] for k in
           ("regular_rows_on_non_trading_days", "missing_regular_sessions", "jumps", "possible_split", "thin_volume_dates", "latest_session_not_complete"))}
    print(f"\nDATA-QUALITY FLAGS ({len(bad)} of {sum(1 for v in per_symbol.values() if 'flags' in v)} symbols):")
    for s, f in list(bad.items())[:15]:
        print(" ", s, json.dumps({k: v for k, v in f.items() if v}, default=str))
    print("\nLARGEST DIFFERENCES (verified regular-close move vs stored legacy):")
    diff = sorted((r for r in evrecs if r.get("stored_legacy") is not None and r["status"] == "GRADED"),
                  key=lambda r: abs(r["stored_legacy"] - r["verified_move"]), reverse=True)[:12]
    for r in diff:
        print(f"  {r['symbol']:6s} {r['earnings_date']} {r['kind']} stored {r['stored_legacy']:+.2f} verified {r['verified_move']:+.2f} | {r['vs_stored']}")
    print("\nPROVISIONAL ACCURACY ON THIS SAMPLE (eligible pre-release snapshots, verified regular-close outcomes only):")
    for m, v in acc.items():
        print(f"  {v['label']:22s} eligible {v['eligible']} graded {v['graded']} | BUY {v['BUY']} SELL {v['SELL']} NEUTRAL calls {v['NEUTRAL_calls']} | baseline {v['baseline']}")


if __name__ == "__main__":
    main()
