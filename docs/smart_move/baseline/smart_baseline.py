"""Smart Move offline price-only baseline (research code; imports nothing from the application except the calendar/window helpers).

Everything that decides a result is read from the frozen pre-registration (prereg_baseline_v1.json, SHA-256 in its .sha256 sidecar); the code
refuses to run if the file does not match its hash. Validation and test rows are never loaded with their outcomes and no price after the
development truncation date is ever read: see load_events() and load_series().
"""
import csv
import hashlib
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
BACKEND = HERE.parents[2] / "backend"
FEATURE_COLS = ("ret20", "rel20", "runup5", "rv20", "adv20", "mean4")


class SealedDataError(RuntimeError):
    pass


class PreregError(RuntimeError):
    pass


def _cal():
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import earnings_outcomes as eo
    import market_calendar as mc
    return mc, eo


def sha256_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load_prereg(path=None):
    path = Path(path or HERE / "prereg_baseline_v1.json")
    want = Path(str(path) + ".sha256").read_text(encoding="ascii").split()[0]
    got = sha256_file(path)
    if got != want:
        raise PreregError("pre-registration hash mismatch: %s != %s" % (got, want))
    return json.loads(path.read_bytes().decode("ascii")), got


def d_(s):
    return date.fromisoformat(str(s)[:10])


def split_of(day, prereg):
    for k in ("dev", "validation", "test"):
        a, b = prereg["splits"][k]
        if a <= day <= b:
            return k
    return None


# ------------------------------------------------------------------ sealed loading
def load_events(cutoffs_csv, prereg, split="dev"):
    """Eligible exploratory events of ONE split. Only 'dev' is available in this run; sealed rows are skipped without their outcome
    columns ever being read into an event object."""
    if split != "dev":
        raise SealedDataError("split %r is sealed in this run; unsealing needs a new written approval and a new pre-registration hash" % split)
    cats = set(prereg["event_set"]["categories"])
    out = []
    with open(cutoffs_csv, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if split_of(row["earnings_date"], prereg) != "dev":
                continue                                     # sealed row: dropped before anything else is looked at
            if row["category"] not in cats:
                continue
            out.append(row)
    return out


def load_series(candles_dir, symbols, until):
    """Regular-session rows only, truncated at `until` (inclusive): later prices are never read into memory."""
    series = {}
    for s in symbols:
        f = Path(candles_dir) / (s + ".json")
        if not f.exists():
            continue
        rows = json.loads(f.read_text(encoding="utf-8"))
        ser = {}
        for r in rows:
            if str(r.get("market_time")) != "r":
                continue
            day = d_(r["date"])
            if day > until:
                continue
            vol = float(r["volume"]) if r.get("volume") not in (None, "") else None
            ser[day] = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), vol)
        series[s] = ser
    return series


def verify_candle_index(candles_dir, index_json, symbols):
    idx = json.loads(Path(index_json).read_text(encoding="utf-8"))
    bad = [s for s in symbols if sha256_file(Path(candles_dir) / (s + ".json")) != idx[s + ".json"]["sha256"]]
    return bad


# ------------------------------------------------------------------ calendar helpers
def utc_dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def last_usable_session(cutoff, buffer_min):
    mc, _ = _cal()
    day = cutoff.astimezone(ET).date()
    while not (mc.is_trading_day(day) and mc.session_close_utc(day) + timedelta(minutes=buffer_min) <= cutoff):
        day -= timedelta(days=1)
    return day


def window(ed, kind):
    _, eo = _cal()
    return eo.reaction_window(ed, kind)


def reaction_return(series, ed, kind):
    """(next-session close-to-close move in %, base session, reaction session); None when either regular close is missing."""
    base, react = window(ed, kind)
    c0 = series[base][3] if base in series else None
    c1 = series[react][3] if react in series else None
    if not c0 or not c1:
        return None, base, react
    return round((c1 / c0 - 1) * 100, 2), base, react


# ------------------------------------------------------------------ suspect windows and features
def find_suspects(ser, reaction_sessions, threshold_pct):
    days = sorted(ser)
    out = set()
    for p, q in zip(days, days[1:]):
        if ser[p][3] and abs(ser[q][3] / ser[p][3] - 1) * 100 > threshold_pct and q not in reaction_sessions:
            out.add(q)
    return out


def features(ser, last, suspects, spy, spy_suspects=frozenset()):
    """Features for one event; every window ends at or before `last`. Returns (values, missing_reasons, max_source_date)."""
    ds = [d for d in sorted(ser) if d <= last]
    val, why, used = {k: None for k in FEATURE_COLS}, {}, []
    if not ds or ds[-1] != last:
        return val, {k: "NO_PRICE_ON_LAST_SESSION" for k in FEATURE_COLS}, None

    def ret(n, series_, sus):
        dd = [d for d in sorted(series_) if d <= last]
        if len(dd) < n + 1 or dd[-1] != last:
            return None, "SHORT_HISTORY"
        if any(q in sus for q in dd[-n:]):
            return None, "SUSPECT_PRICE_JUMP_IN_WINDOW"
        return series_[dd[-1]][3] / series_[dd[-1 - n]][3] - 1, None

    for name, n in (("ret20", 20), ("runup5", 5)):
        v, r = ret(n, ser, suspects)
        val[name] = v
        if r:
            why[name] = r
        else:
            used.append(ds[-1 - n])
    v, r = ret(20, ser, suspects)
    s20, rs = ret(20, spy, spy_suspects)
    if v is not None and s20 is not None:
        val["rel20"] = v - s20
    else:
        why["rel20"] = r or rs
    if len(ds) >= 21 and not any(q in suspects for q in ds[-20:]):
        rets = [ser[ds[i]][3] / ser[ds[i - 1]][3] - 1 for i in range(len(ds) - 20, len(ds))]
        val["rv20"] = statistics.pstdev(rets)
    else:
        why["rv20"] = "SUSPECT_PRICE_JUMP_IN_WINDOW" if len(ds) >= 21 else "SHORT_HISTORY"
    if len(ds) >= 20 and not any(q in suspects for q in ds[-20:]) and all(ser[d][4] for d in ds[-20:]):
        val["adv20"] = statistics.mean(ser[d][3] * ser[d][4] for d in ds[-20:])
    else:
        why["adv20"] = "SUSPECT_PRICE_JUMP_IN_WINDOW" if any(q in suspects for q in ds[-20:]) else "SHORT_HISTORY_OR_NO_VOLUME"
    return val, why, ds[-1]


def prior_mean4(sym, cutoff, ev_returns, suspects, buffer_min):
    """Mean of the last up to 4 earlier reactions of `sym` that had closed (plus buffer) before the cutoff; None if fewer than 3 usable."""
    mc, _ = _cal()
    got = []
    for (ed, base, react, ret_pct) in ev_returns.get(sym, []):
        if mc.session_close_utc(react) + timedelta(minutes=buffer_min) > cutoff:
            continue
        if any(base < q <= react for q in suspects):
            continue
        got.append((ed, ret_pct))
    got = [r for _, r in sorted(got)][-4:]
    return (statistics.mean(got) / 100.0, len(got)) if len(got) >= 3 else (None, len(got))


# ------------------------------------------------------------------ rules
def votes(f, prereg, scale=1.0):
    v = prereg["votes"]
    out, bull, bear = {}, 0, 0
    for name, key in (("trend", "ret20"), ("relative_strength", "rel20"), ("earnings_history", "mean4")):
        x = f.get(key)
        if x is None:
            out[name] = None
            continue
        plus, minus = v[name]["plus"] * scale, v[name]["minus"] * scale
        out[name] = 1 if x > plus else -1 if x < minus else 0
        bull += out[name] == 1
        bear += out[name] == -1
    return out, bull, bear


def decide(bull, bear, available, adv20, tradable, prereg, direction="continuation"):
    min_adv = 20_000_000
    if not tradable:
        return "NO_TRADE", "NOT_TRADABLE"
    if available < 2:
        return "NO_TRADE", "INSUFFICIENT_COVERAGE"
    if adv20 is None or adv20 < min_adv:
        return "NO_TRADE", "ILLIQUID_OR_UNKNOWN"
    if bull >= 2 and bear == 0:
        sig = "BUY"
    elif bear >= 2 and bull == 0:
        sig = "SELL"
    else:
        return "NO_TRADE", "BELOW_MARGIN_OR_CONFLICT"
    if direction == "contrarian":
        sig = "SELL" if sig == "BUY" else "BUY"
    return sig, "RULE"


def order_deadline_ok(cutoff, base, minutes):
    mc, _ = _cal()
    return mc.is_trading_day(base) and cutoff < mc.session_close_utc(base) - timedelta(minutes=minutes)


# ------------------------------------------------------------------ costs and statistics
def side_bps(adv):
    return 3 if adv is not None and adv >= 200e6 else 8 if adv is not None and adv >= 20e6 else 20


def net_return(sign, move_pct, adv, mult=1.0, slip=1.0):
    cost = mult * (2 * (side_bps(adv) * slip + 1) + (1 if sign < 0 else 0)) / 100.0
    return sign * move_pct - cost


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(c - h, 3), round(c + h, 3)]


def boot_mean(vals, groups, reps, seed):
    """Mean with a 95 % interval from a cluster bootstrap (clusters = groups, e.g. ISO week of the earnings date)."""
    if not vals:
        return None
    by = defaultdict(list)
    for v, g in zip(vals, groups):
        by[g].append(v)
    keys = list(by)
    rnd = random.Random(seed)
    means = []
    for _ in range(reps):
        pooled = [x for k in (rnd.choice(keys) for _ in keys) for x in by[k]]
        means.append(statistics.mean(pooled))
    means.sort()
    return [round(statistics.mean(vals), 3), round(means[int(0.025 * reps)], 3), round(means[int(0.975 * reps)], 3)]


def cls_of(move, band):
    return "UP" if move > band else "DOWN" if move < -band else "FLAT"


def summarise(calls, prereg, label):
    """calls: list of dicts with sig, move, spy_move, adv, week, outcome."""
    band = prereg["label"]["flat_band_pct"]
    iv = prereg["intervals"]
    dec = [c for c in calls if c["sig"] in ("BUY", "SELL")]
    out = {"name": label, "eligible": len(calls), "BUY": sum(c["sig"] == "BUY" for c in calls), "SELL": sum(c["sig"] == "SELL" for c in calls),
           "NO_TRADE": sum(c["sig"] == "NO_TRADE" for c in calls)}
    out["coverage"] = round(len(dec) / len(calls), 3) if calls else None
    out["abstention_rate"] = round(1 - len(dec) / len(calls), 3) if calls else None
    mat = {s: Counter() for s in ("BUY", "SELL", "NO_TRADE")}
    for c in calls:
        mat[c["sig"]][c["outcome"]] += 1
    out["confusion"] = {s: {k: mat[s].get(k, 0) for k in ("UP", "FLAT", "DOWN")} for s in mat}
    hit = sum(1 for c in dec if (c["sig"] == "BUY" and c["outcome"] == "UP") or (c["sig"] == "SELL" and c["outcome"] == "DOWN"))
    nonflat = sum(1 for c in dec if c["outcome"] != "FLAT")
    out["decided"] = len(dec)
    out["flat_among_decided"] = sum(1 for c in dec if c["outcome"] == "FLAT")
    out["strict_hit_rate"] = round(hit / len(dec), 3) if dec else None
    out["strict_hit_ci95"] = wilson(hit, len(dec), iv["wilson_z"])
    out["strict_hit_ci_bonferroni"] = wilson(hit, len(dec), iv["bonferroni_two_rules_z"])
    out["direction_only_hit_rate"] = round(hit / nonflat, 3) if nonflat else None
    out["direction_only_ci95"] = wilson(hit, nonflat, iv["wilson_z"])
    out["direction_only_n"] = nonflat
    sign = lambda c: 1 if c["sig"] == "BUY" else -1
    b = iv["bootstrap"]
    grid = {}
    for m in prereg["costs"]["sensitivity"]["cost_multipliers"]:
        vals = [net_return(sign(c), c["move"], c["adv"], m) for c in dec]
        grid["cost_x%s" % m] = boot_mean(vals, [c["week"] for c in dec], b["reps"], b["seed"])
    for sl in prereg["costs"]["sensitivity"]["slippage_only_multipliers"]:
        vals = [net_return(sign(c), c["move"], c["adv"], 1.0, sl) for c in dec]
        grid["slippage_x%s" % sl] = boot_mean(vals, [c["week"] for c in dec], b["reps"], b["seed"])
    out["net_mean_pct_per_call_[mean,lo95,hi95]"] = grid
    adj = [net_return(sign(c), c["move"] - c["spy_move"], c["adv"], 1.0) for c in dec]
    out["spy_adjusted_net_mean_pct_cost_x1_[mean,lo95,hi95]"] = boot_mean(adj, [c["week"] for c in dec], b["reps"], b["seed"])
    return out
