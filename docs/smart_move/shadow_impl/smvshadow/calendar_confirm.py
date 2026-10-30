"""Vendor-level calendar verification (isolated; changes no production behaviour, makes no request).

Statuses, from weakest to strongest:
  UNKNOWN, STALE, MOVED, ESTIMATED  -> never eligible
  VENDOR_CONFIRMED  -> Unusual Whales row with its raw source flag == 'company' AND a second, different vendor with the same date and the same BMO/AMC timing, both
                       observations fresh, no contradicting observation, and a documented vendor-dependency record for the pair. This is VENDOR AGREEMENT, not direct
                       company confirmation, and is labelled so everywhere.
  COMPANY_VERIFIED  -> direct company evidence (press release / IR page captured with its hash) matching the date and timing. Separate, higher confidence.
Both eligible statuses may be used for shadow predictions only.

The provider's own flag is preserved verbatim (`vendor_flag`). The old app field `date_confirmed` (hard-coded True in uw_earnings_feed._row) is never read here: a row
with no `source` has vendor_flag None and can never reach VENDOR_CONFIRMED. `calendar_observed_at` (when a vendor row was retrieved) is a different clock from the
prediction snapshot time, which is carried alongside and never used for freshness.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from . import calendar_validation as cv

UTC = timezone.utc
VENDOR_OF = {"UW_CALENDAR": "UNUSUAL_WHALES", "UW_ROW": "UNUSUAL_WHALES", "FINVIZ": "FINVIZ", "FINNHUB": "FINNHUB"}
UNKNOWN, STALE, MOVED, ESTIMATED = "UNKNOWN", "STALE", "MOVED", "ESTIMATED"
VENDOR_CONFIRMED, COMPANY_VERIFIED = "VENDOR_CONFIRMED", "COMPANY_VERIFIED"
ELIGIBLE = (VENDOR_CONFIRMED, COMPANY_VERIFIED)

# Documented dependency records. Status is what we can actually support: none of these vendors documents its upstream in the material we hold, so independence is
# UNPROVEN and a shared upstream is POSSIBLE. A pair without a record cannot confirm anything.
DEPENDENCY = {
    frozenset(["UNUSUAL_WHALES", "FINVIZ"]): {"status": "UNPROVEN_INDEPENDENCE", "documented": "2026-10-31",
                                              "note": "Upstream of both calendars is not stated in the provider documents held; a shared upstream (company or exchange feed) is possible. Agreement is vendor agreement only."},
    frozenset(["UNUSUAL_WHALES", "FINNHUB"]): {"status": "UNPROVEN_INDEPENDENCE", "documented": "2026-10-31",
                                               "note": "Upstream of both calendars is not stated in the provider documents held; a shared upstream is possible. Agreement is vendor agreement only."},
}


def sha_of(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CalObs:
    source: str                    # UW_CALENDAR | UW_ROW | FINVIZ | FINNHUB
    symbol: str
    date: date
    label: str | None              # provider's own session label as received ('premarket', 'bmo', ...)
    clock: str | None              # clock time as received ('4:30 PM'), Finviz only
    vendor_flag: str | None        # raw provider flag, verbatim ('company' | 'estimation' | None when absent)
    raw_sha256: str
    observed_at: datetime          # retrieval time = calendar_observed_at
    published_at: datetime | None = None

    @property
    def vendor(self):
        return VENDOR_OF[self.source]

    def session(self):
        """BMO | AMC | UNKNOWN from label first, else clock. A contradiction between label and clock is reported by the caller."""
        a = cv.normalise_session(self.label) if self.label else "UNKNOWN"
        b = cv.normalise_session(self.clock) if self.clock else "UNKNOWN"
        if a != "UNKNOWN" and b != "UNKNOWN" and a != b:
            return "CONTRADICTED"
        return a if a != "UNKNOWN" else b


def _need(v, what):
    if v is None:
        raise ValueError("missing " + what)
    return v


def from_uw_row(row: dict, observed_at: datetime, raw_sha256: str, source="UW_CALENDAR") -> CalObs:
    """Unusual Whales earnings row. `source` is read from the raw row and preserved; `date_confirmed` and anything app-derived is ignored."""
    flag = row.get("source")
    flag = str(flag).strip().lower() if flag not in (None, "") else None
    return CalObs(source, str(_need(row.get("symbol"), "symbol")).upper(), date.fromisoformat(str(_need(row.get("report_date"), "report_date"))[:10]),
                  row.get("report_time"), None, flag, raw_sha256, observed_at)


def from_finviz(symbol: str, raw: str, observed_at: datetime, raw_sha256: str) -> CalObs | None:
    """Finviz 'M/D/YYYY h:mm:ss AM/PM' (time optional)."""
    s = (raw or "").strip()
    if not s or s == "-":
        return None
    parts = s.split(" ", 1)
    try:
        d = datetime.strptime(parts[0], "%m/%d/%Y").date()
    except ValueError:
        return None
    clock = None
    if len(parts) > 1 and parts[1].strip():
        t = parts[1].strip()
        for fmt in ("%I:%M:%S %p", "%I:%M %p"):
            try:
                clock = datetime.strptime(t, fmt).strftime("%I:%M %p").lstrip("0")
                break
            except ValueError:
                continue
    return CalObs("FINVIZ", symbol.upper(), d, None, clock, None, raw_sha256, observed_at)


def from_finnhub(row: dict, observed_at: datetime, raw_sha256: str) -> CalObs:
    hour = {"bmo": "bmo", "amc": "amc"}.get(str(row.get("hour") or "").lower())          # 'dmh' (during market hours) or empty stays unknown
    return CalObs("FINNHUB", str(_need(row.get("symbol"), "symbol")).upper(), date.fromisoformat(str(_need(row.get("date"), "date"))[:10]), hour, None, None, raw_sha256, observed_at)


class ObservationLog:
    """Append-only, hash-chained list of observations. No update or delete method exists; an identical observation is ignored (idempotent)."""

    def __init__(self):
        self._rows, self._keys = [], set()

    def append(self, o: CalObs) -> bool:
        key = (o.source, o.symbol, str(o.date), o.raw_sha256, o.observed_at.isoformat())
        if key in self._keys:
            return False
        prev = self._rows[-1][1] if self._rows else "0" * 64
        self._rows.append((o, sha_of({"prev": prev, "o": [o.source, o.symbol, str(o.date), o.label, o.clock, o.vendor_flag, o.raw_sha256, o.observed_at.isoformat()]})))
        self._keys.add(key)
        return True

    def for_symbol(self, symbol, until: datetime | None = None):
        return [o for o, _ in self._rows if o.symbol == symbol and (until is None or o.observed_at <= until)]

    def head(self):
        return self._rows[-1][1] if self._rows else "0" * 64

    def verify(self) -> bool:
        prev = "0" * 64
        for o, h in self._rows:
            if h != sha_of({"prev": prev, "o": [o.source, o.symbol, str(o.date), o.label, o.clock, o.vendor_flag, o.raw_sha256, o.observed_at.isoformat()]}):
                return False
            prev = h
        return True

    def __len__(self):
        return len(self._rows)


def _latest_per_source(obs):
    out = {}
    for o in obs:
        if o.source not in out or o.observed_at > out[o.source].observed_at:
            out[o.source] = o
    return out


def _latest_per_vendor(obs):
    out = {}
    for o in obs:
        if o.vendor not in out or o.observed_at > out[o.vendor].observed_at:
            out[o.vendor] = o
    return out


def evaluate(symbol, obs, now: datetime, today: date, *, prior_event_date: date | None = None, prediction_snapshot_at: datetime | None = None,
             prior_prediction_exists: bool = False, company_evidence: dict | None = None, dependency=DEPENDENCY):
    """Deterministic calendar evaluation of ONE symbol's next event. Returns a dict; `no_trade_reasons` is empty only when eligible_for_shadow is True."""
    obs = [o for o in obs if o.symbol == symbol and o.observed_at <= now]
    ev = {"symbol": symbol, "status": UNKNOWN, "event_date": None, "kind": "UNKNOWN", "timing_status": cv.T_UNKNOWN, "confidence": None, "reasons": [], "no_trade_reasons": [],
          "eligible_for_shadow": False, "calendar_observed_at": None, "prediction_snapshot_at": prediction_snapshot_at, "moved_from": None, "dependency": None, "vendors": []}
    if not obs:
        ev["reasons"] = ev["no_trade_reasons"] = ["NO_OBSERVATION"]
        return ev
    latest = _latest_per_source(obs)
    dates = sorted({o.date for o in latest.values()})
    if len(dates) > 1 and (dates[-1] - dates[0]).days > 1:
        ev["reasons"] = ev["no_trade_reasons"] = ["DATE_CONFLICT_OVER_1_DAY"]
        ev["calendar_observed_at"] = max(o.observed_at for o in latest.values())
        return ev
    chosen = max(latest.values(), key=lambda o: o.observed_at).date
    on = [o for o in latest.values() if o.date == chosen]
    ev["event_date"], ev["vendors"] = chosen, sorted({o.vendor for o in on})
    newest = max(o.observed_at for o in on)
    ev["calendar_observed_at"] = newest
    reasons, nt = [], []
    if len(dates) > 1:
        reasons.append("DATE_DISAGREEMENT_1_DAY")
        nt.append("DATE_DISAGREEMENT_1_DAY")
    if prior_event_date and prior_event_date != chosen:
        ev["moved_from"] = prior_event_date
        reasons.append("MOVED")
        if prior_prediction_exists:
            reasons.append("MOVED_AFTER_PREDICTION")
    days_to = max((chosen - today).days, 0)
    limit = cv.freshness_limit(days_to)

    def fresh(o):
        return now - o.observed_at <= limit and now - o.observed_at <= cv.CONFIRM_MAX_AGE

    # timing, per vendor
    sessions = {o.source: o.session() for o in on}
    if "CONTRADICTED" in sessions.values():
        reasons.append("LABEL_CLOCK_CONTRADICTION")
    known = {k: v for k, v in sessions.items() if v in ("BMO", "AMC")}
    kinds = set(known.values())
    timing_conflict = len(kinds) > 1 or "CONTRADICTED" in sessions.values()
    kind = next(iter(kinds)) if len(kinds) == 1 else "UNKNOWN"
    if timing_conflict:
        nt.append("TIMING_CONTRADICTED")
    elif not known:
        nt.append("UNKNOWN_TIMING")

    # direct company evidence
    if company_evidence:
        ce_ok = company_evidence.get("date") == chosen and company_evidence.get("session") in ("BMO", "AMC") and company_evidence.get("sha256") and \
            now - company_evidence["retrieved_at"] <= cv.CONFIRM_MAX_AGE
        if company_evidence.get("date") != chosen:
            ev["reasons"] = reasons + ["COMPANY_EVIDENCE_CONTRADICTS_VENDORS"]
            ev["no_trade_reasons"] = nt + ["COMPANY_EVIDENCE_CONTRADICTS_VENDORS"]
            return ev
        if known and company_evidence.get("session") in ("BMO", "AMC") and company_evidence["session"] not in kinds:
            ev["reasons"] = reasons + ["COMPANY_EVIDENCE_CONTRADICTS_VENDORS"]
            ev["no_trade_reasons"] = sorted(set(nt + ["TIMING_CONTRADICTED", "COMPANY_EVIDENCE_CONTRADICTS_VENDORS"]))
            return ev
        if ce_ok and not timing_conflict and (not known or company_evidence["session"] in kinds):
            ev.update(status=COMPANY_VERIFIED, kind=company_evidence["session"], timing_status=cv.T_CONFIRMED, confidence="COMPANY_VERIFIED", reasons=reasons + ["COMPANY_EVIDENCE"],
                      no_trade_reasons=[], eligible_for_shadow=True)
            return ev

    if now - newest > limit:
        ev.update(status=STALE, reasons=reasons + ["CALENDAR_OBSERVATION_STALE"], no_trade_reasons=nt + ["STALE_CALENDAR"])
        return ev

    uw = [o for o in on if o.vendor == "UNUSUAL_WHALES"]
    uw_company = [o for o in uw if o.vendor_flag == "company"]
    others = _latest_per_vendor([o for o in on if o.vendor != "UNUSUAL_WHALES"])
    if not uw:
        nt.append("SINGLE_VENDOR" if not others or len(others) < 2 else "UW_FLAG_MISSING")
        reasons.append("NO_UW_OBSERVATION")
    elif not uw_company:
        flag = _latest_per_vendor(uw)["UNUSUAL_WHALES"].vendor_flag
        nt.append("SOURCE_ESTIMATION" if flag == "estimation" else "UW_FLAG_MISSING")
        reasons.append("UW_FLAG_" + (flag or "ABSENT").upper())
    if len({o.vendor for o in on}) < 2:
        nt.append("SINGLE_VENDOR")
    status = ESTIMATED
    if uw_company and others and not timing_conflict and not (len(dates) > 1):
        uw_obs = max(uw_company, key=lambda o: o.observed_at)
        partner = None
        for v, po in sorted(others.items()):
            pair = frozenset(["UNUSUAL_WHALES", v])
            rec = dependency.get(pair)
            if not rec or not rec.get("note"):
                nt.append("DEPENDENCY_UNDOCUMENTED")
                continue
            if po.raw_sha256 == uw_obs.raw_sha256:
                nt.append("SHARED_PAYLOAD")                      # the same bytes cannot count as two vendors
                continue
            if po.session() not in ("BMO", "AMC") or uw_obs.session() not in ("BMO", "AMC"):
                nt.append("UNKNOWN_TIMING")
                continue
            if po.session() != uw_obs.session():
                continue
            if not (fresh(uw_obs) and fresh(po)):
                nt.append("PARTNER_OBSERVATION_STALE")
                reasons.append("PARTNER_OBSERVATION_STALE")
                continue
            partner = (v, rec)
            break
        if partner:
            status = VENDOR_CONFIRMED
            ev["dependency"] = {"pair": sorted(["UNUSUAL_WHALES", partner[0]]), "status": partner[1]["status"], "note": partner[1]["note"]}
            reasons.append("VENDOR_AGREEMENT_NOT_COMPANY_CONFIRMATION")
            nt = [x for x in nt if x not in ("SINGLE_VENDOR", "UW_FLAG_MISSING", "UNKNOWN_TIMING", "DEPENDENCY_UNDOCUMENTED", "PARTNER_OBSERVATION_STALE", "SHARED_PAYLOAD")]
    if status == ESTIMATED and ev["moved_from"]:
        status = MOVED
        nt.append("MOVED_UNCONFIRMED")
    ev.update(status=status, reasons=reasons, kind=kind if status in ELIGIBLE or kind != "UNKNOWN" else "UNKNOWN")
    ev["timing_status"] = (cv.T_CONFIRMED if status in ELIGIBLE else cv.T_CONFLICT if timing_conflict else cv.T_SINGLE if known else cv.T_UNKNOWN)
    if status in ELIGIBLE:
        ev["confidence"] = status
        if timing_conflict or kind == "UNKNOWN":
            status = ev["status"] = ESTIMATED
            ev["confidence"] = None
            nt.append("TIMING_CONTRADICTED" if timing_conflict else "UNKNOWN_TIMING")
    ev["no_trade_reasons"] = sorted(set(nt)) if ev["status"] not in ELIGIBLE else []
    ev["eligible_for_shadow"] = ev["status"] in ELIGIBLE and not ev["no_trade_reasons"]
    return ev


def dependency_evidence(history):
    """history: iterable of (symbol, date, {vendor: date_seen, ...}). Counts where two vendors differ. Zero disagreement proves nothing about independence."""
    n = dis = 0
    for _, _, seen in history:
        vals = list(seen.values())
        if len(vals) >= 2:
            n += 1
            dis += len(set(vals)) > 1
    return {"events_with_two_vendors": n, "disagreements": dis,
            "reading": "DISAGREEMENTS_OBSERVED: the vendors differ at least sometimes" if dis else "NO_DISAGREEMENT_SEEN: independence stays UNPROVEN (identical data would look the same)"}
