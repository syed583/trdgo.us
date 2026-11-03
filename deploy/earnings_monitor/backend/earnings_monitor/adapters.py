"""Production adapters. READ paths use only in-memory / database caches (swr.peek never builds or refreshes anything), so they cannot start a provider request. The provider paths (calendar refresh with a second
vendor, event-evidence refresh, outcome verification) are reached only through ProviderAccess, which needs the owner's approval for each purpose. Modules of the live app are imported lazily, so importing this
file loads no provider code.

What the eight "model" sources really are (read from their code): formula models. None of them calls an external AI service; each scores `earn:equity:{SYM}` and `earn:trade:default:{SYM}`, which are built by
earnings_equity_service.get_analysis and earnings_trade_service.get_summary from Unusual Whales (and, for a few inputs, Finnhub) data. So a genuine stored prediction exists whenever those caches are warm, and
what needs separate provider permission is refreshing that DATA, never running a model."""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Callable, Optional

from .service import Adapters, HORIZON_DAYS
from .smart_move import SmartMoveSource

UTC = timezone.utc
MODEL_KEYS = {"claude": "v3:scan:v2:{d}", "claude_pro": "claudepro:scan:{d}", "gemini": "gemini:scan:{d}", "grok": "grok:scan:{d}", "chatgpt": "chatgpt:scan:{d}", "meta": "meta:scan:{d}",
              "kimi": "kimi:scan:{d}", "ped": "ped:scan:{d}"}
STORED_ID = {"claude": "claude_new"}                       # the daily snapshot table keeps Claude under its older id
LABELS = {"claude": "Claude", "claude_pro": "Claude Pro", "gemini": "Gemini", "grok": "Grok", "chatgpt": "ChatGPT", "meta": "Meta", "kimi": "Kimi", "ped": "Pre-Earnings Direction",
          "smart_move": "Smart Move score40-v1"}
MODEL_META = {
    "claude": {"module": "claude_last_service", "what": "v3 two-sided pre-earnings direction model", "extra_inputs": []},
    "claude_pro": {"module": "claude_pro_service", "what": "six-block refined direction model", "extra_inputs": []},
    "gemini": {"module": "gemini_service", "what": "Anas Engine v2, 11 parameters", "extra_inputs": []},
    "grok": {"module": "grok_service", "what": "9 research-strength factors", "extra_inputs": ["Finnhub analyst dispersion (detail page only)"]},
    "chatgpt": {"module": "chatgpt_service", "what": "16-feature direction model V1", "extra_inputs": ["Finnhub analyst dispersion (detail page only)"]},
    "meta": {"module": "meta_service", "what": "11-factor / 100-point direction model", "extra_inputs": []},
    "kimi": {"module": "kimi_service", "what": "12-factor direction model", "extra_inputs": ["Finnhub price-target changes (detail page only)", "SPY trend + VIX once per scan"]},
    "ped": {"module": "pre_earnings_direction_service", "what": "v2 nine-parameter direction model", "extra_inputs": []},
}
SHARED_INPUTS = ["earn:equity:{SYM}  <- earnings_equity_service.get_analysis (Unusual Whales, some Finnhub)", "earn:trade:default:{SYM}  <- earnings_trade_service.get_summary (Unusual Whales)"]


def _session(row: dict) -> str:
    raw = str(row.get("reporting_time") or row.get("report_timing") or row.get("time_label") or row.get("timing_label") or "").lower()
    if raw in ("bmo",) or "before" in raw or "pre" in raw:
        return "BMO"
    if raw in ("amc",) or "after" in raw or "post" in raw:
        return "AMC"
    return "UNKNOWN"


def _iso(epoch: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(epoch, UTC).isoformat() if epoch else None


def _db_session():
    from database import SessionLocal
    return SessionLocal()


class ProdAdapters(Adapters):
    model_labels = LABELS

    def __init__(self, days: int = HORIZON_DAYS, smart_move: Optional[SmartMoveSource] = None, session_factory: Optional[Callable] = None, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.days, self.smart = days, smart_move if smart_move is not None else SmartMoveSource.from_env()
        self._session, self.clock = session_factory or _db_session, clock
        self._moves: dict = {}                                    # symbol -> {report date: move_pct} from the last verify_outcome in this process

    # ------------------------------------------------------------ cache reads
    def _scans(self) -> dict:
        import swr
        out = {}
        for m, tmpl in MODEL_KEYS.items():
            key = tmpl.format(d=self.days)
            res = swr.peek(key)                                   # cache read only: never builds, never refreshes
            if isinstance(res, dict):
                out[m] = (res, swr.held_at(key))
        return out

    def _stored_snapshots(self) -> dict:
        """The latest stored daily snapshot per model / symbol for events still ahead (the existing capture loop writes them). A fallback for a cold cache, labelled as such."""
        try:
            from sqlalchemy import text
            db = self._session()
            try:
                rows = db.execute(text("SELECT model, symbol, earnings_date, signal, score, predicted_at FROM model_predictions WHERE earnings_date >= :d ORDER BY predicted_at"), {"d": date.today().isoformat()}).fetchall()
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            return {}
        back = {v: k for k, v in STORED_ID.items()}
        out: dict = {}
        for model, sym, ed, sig, score, at in rows:
            m = back.get(model, model)
            if m in LABELS and sig in ("BUY", "SELL", "NEUTRAL"):
                at_iso = at.isoformat() if hasattr(at, "isoformat") else str(at)
                if at_iso and "+" not in at_iso[10:] and not at_iso.endswith("Z"):
                    at_iso += "+00:00"
                out.setdefault(m, {})[str(sym).upper()] = {"signal": sig, "score": score, "coverage": None, "tier": None, "as_of": at_iso, "evidence_as_of": None, "source": "model_predictions_snapshot"}
        return out

    def models(self) -> dict:
        out = {}
        cached = self._scans()
        stored = None
        for m, (res, held) in cached.items():
            rows = {}
            for r in res.get("rows") or []:
                s = (r.get("symbol") or "").upper()
                if s:
                    cov = r.get("coverage")                       # the eight services report coverage as a PERCENT (0-100); store it as a fraction so 0.5 can never be misread
                    rows[s] = {"signal": r.get("signal"), "score": r.get("score") if isinstance(r.get("score"), (int, float)) else None,
                               "coverage": round(cov / 100.0, 4) if isinstance(cov, (int, float)) and not isinstance(cov, bool) else None, "tier": r.get("tier"),
                               "as_of": _iso(held), "evidence_as_of": None, "source": "swr_cache"}
            out[m] = rows
        if len(cached) < len(MODEL_KEYS):                         # at least one model's cache is cold: fill ITS gaps from the stored daily snapshots
            stored = self._stored_snapshots()
            for m in MODEL_KEYS:
                if m not in cached:
                    out[m] = stored.get(m, {})
        out["smart_move"] = self.smart.models(self.clock()) if self.smart else {}
        return out

    def calendar(self) -> list:
        seen = {}
        for m, (res, _) in self._scans().items():
            for r in res.get("rows") or []:
                s = (r.get("symbol") or "").upper()
                d = str(r.get("date") or "")[:10]
                if s and d and s not in seen:
                    seen[s] = {"symbol": s, "company": r.get("company"), "date": d, "session": _session(r), "source": "model_scan_cache"}
        return list(seen.values())

    def outcome(self, symbol: str, d: str) -> Optional[dict]:
        """The real post-earnings move: what verify_outcome fetched in this process, else what the existing scoreboard already graded. Both come from daily bars (prior close -> reaction close)."""
        mv = (self._moves.get(symbol) or {}).get(d)
        if mv is not None:
            ok = abs(mv) <= 100
            return {"move_pct": mv, "direction": "BUY" if mv > 0.5 else "SELL" if mv < -0.5 else "FLAT", "available_at": self.clock().isoformat(), "verified": ok, "source": "daily_bars"}
        try:
            from sqlalchemy import text
            db = self._session()
            try:
                r = db.execute(text("SELECT actual_move_pct, actual_direction, evaluated_at FROM model_predictions WHERE symbol=:s AND earnings_date=:d AND actual_move_pct IS NOT NULL "
                                    "ORDER BY evaluated_at DESC LIMIT 1"), {"s": symbol, "d": d}).fetchone()
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            return None
        if not r:
            return None
        return {"move_pct": float(r[0]), "direction": r[1], "available_at": r[2].isoformat() if hasattr(r[2], "isoformat") else str(r[2]), "verified": abs(float(r[0])) <= 100, "source": "scoreboard_graded"}

    def smart_move_plan(self, events: list, now: datetime) -> Optional[dict]:
        return self.smart.plan(events, now) if self.smart else None

    # ------------------------------------------------------------ provider paths (only through ProviderAccess)
    def refresh_calendar(self) -> list:
        import uw_earnings_calendar as cal                         # has its own UW gate and per-date cache
        res = cal.calendar(days=self.days)
        if res.get("status") != "OK":
            raise RuntimeError("calendar refresh returned %s" % res.get("status"))
        fmap = {}
        try:
            import finviz_service as fv                            # a second, independent vendor for verification
            fmap = fv.earnings_map() or {}
        except Exception:  # noqa: BLE001
            fmap = {}
        rows = []
        for r in res.get("rows") or []:
            sym = (r.get("symbol") or "").upper()
            d = str(r.get("date"))[:10]
            sess = r.get("reporting_time") or "UNKNOWN"
            sources = [{"vendor": "UW", "date": d, "session": sess}]
            fvi = fmap.get(sym)
            if fvi and fvi.get("date"):
                t = str(fvi.get("time") or "").lower()
                sources.append({"vendor": "FINVIZ", "date": str(fvi["date"])[:10], "session": "BMO" if "pre" in t or "bmo" in t else "AMC" if "after" in t or "amc" in t else "UNKNOWN"})
            rows.append({"symbol": sym, "company": r.get("company"), "date": d, "session": sess, "source": "uw_calendar", "sources": sources})
        return rows

    def refresh_event(self, symbol: str) -> None:
        """Rebuild the evidence caches the eight models read for ONE symbol, exactly as the app's own detail routes do (earn:equity and earn:trade). The models re-score from them on their normal cycle."""
        import earnings_equity_service as eq_svc
        import earnings_trade_service as ets
        import swr
        sym = symbol.upper()
        fresh = eq_svc.get_analysis(sym)
        if fresh and fresh.get("params"):
            swr._values[f"earn:equity:{sym}"] = (time.time(), fresh)
        trade = ets.get_summary(sym, "default")
        if trade:
            swr._values[f"earn:trade:default:{sym}"] = (time.time(), trade)

    def verify_outcome(self, symbol: str, d: str) -> None:
        import earnings_trade_service as ets
        moves = ets._historical_moves(symbol)                      # earnings history + daily candles: the real prior-close -> reaction-close move
        self._moves[symbol] = {m["date"]: m["move_pct"] for m in moves if m.get("date") and m.get("move_pct") is not None}

    # ------------------------------------------------------------ what is connected, honestly
    def source_report(self) -> dict:
        cached = {}
        try:
            for m, (res, held) in self._scans().items():
                cached[m] = {"rows": len(res.get("rows") or []), "built_at": _iso(held)}
        except Exception:  # noqa: BLE001
            cached = {}
        stored = self._stored_snapshots()
        models = []
        for m, label in LABELS.items():
            if m == "smart_move":
                st = self.smart.status() if self.smart else {"engine_installed": False, "snapshot_dir_configured": False, "how_predictions_arise": "none: not installed"}
                models.append({"id": m, "label": label, "kind": "frozen 40-point engine (score40-v1)", "external_ai_service": False, "stored_predictions_now": bool(self.smart and self.smart.models()), **st,
                               "needs_authorised_execution": "collecting its evidence (3-year bars, earnings history, SPY, sector ETFs) is a separate provider run with its own attestation and 50-attempt cap"})
                continue
            meta = MODEL_META[m]
            models.append({"id": m, "label": label, "kind": "formula model over warmed caches", "what": meta["what"], "external_ai_service": False, "reads": SHARED_INPUTS + meta["extra_inputs"],
                           "scan_cache": cached.get(m), "stored_snapshots": len(stored.get(m, {})), "stored_predictions_now": bool(cached.get(m, {}).get("rows") or stored.get(m)),
                           "needs_authorised_execution": "only to refresh the data it reads (Unusual Whales / Finnhub requests through the shared UW gate); no model run is billed or external"})
        return {"models": models, "note": "Determined from the services' code and the live caches: no model calls an external AI service."}
