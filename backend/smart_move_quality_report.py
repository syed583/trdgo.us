"""Evidence-quality report bundle for the Smart Move quality page (offline, read only). One JSON file that joins, per earnings event: calendar status and eligibility, fiscal-quarter state and source, Snapshot A/B
pairing, news quality and warnings, outcome state and prediction admissibility, plus summaries and the verified-only accuracy report. The bundle says where it came from (`source`) and whether it is synthetic;
the page must never present a synthetic bundle as live. Nothing here fetches, scores or writes anything except the one report file it is asked to create."""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smart_move_accuracy as AC  # noqa: E402
import smart_move_event_records as ER  # noqa: E402
import smart_move_news_audit as NA  # noqa: E402
import smart_move_news_review as NR  # noqa: E402
import smart_move_outcome_audit as OA  # noqa: E402
import smart_move_pipeline as PL  # noqa: E402
import smart_move_snapshot_pairs as SP  # noqa: E402

UTC = timezone.utc
SCHEMA = "smart_move.quality_report.v1"


def _dt(x) -> datetime:
    d = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def evidence_classes(h: dict, pair: dict, n: Optional[dict], o: Optional[dict], admissible: bool, has_pred: bool, bars: bool) -> dict:
    """Four-way state per evidence dimension: VERIFIED (present and passes its point-in-time test), UNVERIFIED (present but not provable or not independent), MISSING (not collected / not yet available),
    INELIGIBLE (collected but unusable for this event). A missing or unverified item is NEVER a positive or negative signal."""
    vs = h["vendor_status"]
    cal = "VERIFIED" if vs in ("VENDOR_CONFIRMED", "COMPANY_VERIFIED") and h["prediction_eligible"] else "UNVERIFIED" if vs in ("ESTIMATED", "MOVED") else "INELIGIBLE" if vs == "STALE" or h.get("vendor_conflict") or not h["prediction_eligible"] else "MISSING"
    qs = h["quarter_state"]
    quarter = "MISSING" if qs == "QUARTER_UNKNOWN" else "UNVERIFIED" if qs == "AMBIGUOUS" or h.get("quarter_known_before_cutoff") is False else "VERIFIED"
    pr = {"PAIRED": "VERIFIED", "UNPAIRED": "MISSING", "NOT_PAIRABLE": "INELIGIBLE"}[pair["state"]]
    ns = (n or {}).get("news_state")
    news = "VERIFIED" if ns == "HAS_VALID_HEADLINES" else "INELIGIBLE" if ns in ("NO_CUTOFF_FOR_EVENT",) else "UNVERIFIED" if ns == "NO_VALID_HEADLINES" else "MISSING"
    price = "VERIFIED" if bars else "MISSING"
    pred = "VERIFIED" if admissible else "INELIGIBLE" if has_pred else "MISSING"
    st = (o or {}).get("state")
    out = "VERIFIED" if st == "OUTCOME_COMPUTABLE" and (o or {}).get("timing_basis") == "EVENT_RECORD" else "UNVERIFIED" if st in ("OUTCOME_COMPUTABLE", "QUARANTINED_CORPORATE_ACTION", "IMPLAUSIBLE_MOVE", "BARS_RETRIEVED_BEFORE_REACTION_SESSION_CLOSED") else "INELIGIBLE" if st == "UNKNOWN_TIMING_NO_OUTCOME_RULE" else "MISSING"
    return {"calendar": cal, "quarter": quarter, "snapshot_pair": pr, "news": news, "price_bars": price, "prediction": pred, "outcome": out}


def build(record_sets: Iterable["ER.EventRecords"], *, news_records: list, companies: dict, bars_by_symbol: dict, predictions: Iterable[dict], estimate_recs: list, load_estimate_rows, as_of,
          source: str = "SAVED_EVIDENCE", synthetic: bool = False, notes: Optional[list] = None, review_by_event: Optional[dict] = None) -> dict:
    as_of = _dt(as_of)
    record_sets = list(record_sets)
    preds = list(predictions)
    heads = []                                                              # (records, head)
    for rs in record_sets:
        for eid in rs.event_ids():
            h = rs.as_of(eid, as_of)                                           # an event first observed after as_of is not part of this view
            if h:
                heads.append((rs, h))
    ev_in = [{"event_id": h["event_id"], "symbol": h["symbol"], "event_date": h["event_date"], "session": h["session"], "cutoff_utc": h["cutoff_utc"], "ending_fiscal_quarter": h["ending_fiscal_quarter"],
              "quarter_state": h["quarter_state"], "vendors": h.get("vendors") or []} for _, h in heads]
    news = NA.audit(ev_in, news_records, companies)
    news_by = {e["event_id"]: e for e in news["events"]}
    validated = []
    for p in preds:
        for rs, _ in heads:
            if rs.event_ids(p["symbol"]):
                validated.append(PL.validate_prediction(p, rs))
                break
        else:
            validated.append({"symbol": p["symbol"], "earnings_date": str(p["earnings_date"])[:10], "prediction_version": p.get("version"), "event_id": None, "event_version": None, "valid": False,
                              "reasons": ["EVENT_NOT_RECORDED_AT_CAPTURE"], "signals": {k: (v or {}).get("signal") for k, v in (p.get("engine") or {}).items()}})
    pred_by_ev: dict = {}
    for p in preds:
        pred_by_ev.setdefault((p["symbol"], str(p["earnings_date"])[:10]), []).append(p)
    outc = OA.audit(ev_in, bars_by_symbol, pred_by_ev, as_of)
    out_by = {e["event_id"]: e for e in outc["events"]}
    valid_by = {}
    for v in validated:
        if v["valid"]:
            valid_by[(v["symbol"], v["earnings_date"])] = v
    capture = {}
    for rs in record_sets:
        cp = PL.capture_plan(rs, as_of, preds)
        capture.update({r["event_id"]: "CAPTURE_WINDOW_OPEN_NO_PREDICTION" for r in cp["capture_now"]})
        capture.update({r["event_id"]: "WINDOW_CLOSED_NO_ADMISSIBLE_PREDICTION" for r in cp["missed"]})
        capture.update({r["event_id"]: "WAITING_FOR_WINDOW" for r in cp["waiting"]})
    rows, comp_rows = [], []
    for rs, h in heads:
        pair = SP.pair_event(rs, h["event_id"], estimate_recs, load_estimate_rows, as_of)
        n, o = news_by.get(h["event_id"]), out_by.get(h["event_id"])
        comp = PL.event_completeness(h, pair=pair, news=n, bars=bars_by_symbol.get(h["symbol"]))
        comp_rows.append(comp)
        qs = h.get("quarter_source") or {}
        has_pred = (h["symbol"], h["event_date"]) in pred_by_ev
        admissible = (h["symbol"], h["event_date"]) in valid_by
        cap = capture.get(h["event_id"], "NOT_ELIGIBLE" if not h["prediction_eligible"] else "NO_ACTION")
        if admissible:
            cap = "CAPTURED_ADMISSIBLE"
        elif has_pred:
            cap = "PREDICTION_NOT_ADMISSIBLE"
        sa, sb = pair.get("snapshot_a"), pair.get("snapshot_b")
        rows.append({"event_id": h["event_id"], "symbol": h["symbol"], "company": companies.get(h["symbol"]), "event_date": h["event_date"], "session": h["session"], "version": h["version"], "status": h["status"],
                     "vendor_status": h["vendor_status"], "vendor_reasons": h["vendor_reasons"], "observed_at": h["observed_at"], "release_basis": h["release_basis"], "release_time_verified": h["release_time_verified"],
                     "cutoff_utc": h["cutoff_utc"], "quarter_state": h["quarter_state"], "ending_fiscal_quarter": h["ending_fiscal_quarter"], "quarter_reason": h["quarter_reason"],
                     "quarter_retrieved_at": qs.get("retrieved_at"), "quarter_payload_sha256": (qs.get("payload_sha256") or "")[:12] or None, "quarter_known_before_cutoff": h.get("quarter_known_before_cutoff"),
                     "prediction_eligible": h["prediction_eligible"], "ineligible_reasons": h["ineligible_reasons"], "analyst_pairing_eligible": h["analyst_pairing_eligible"], "pairing_state": pair["state"],
                     "pairing_reasons": pair["reasons"], "news_state": (n or {}).get("news_state"), "news_valid": (n or {}).get("valid", 0), "news_warnings": NR.event_warnings(n) if n else [],
                     "outcome_state": (o or {}).get("state"), "outcome_class": (o or {}).get("outcome_class"), "outcome_timing_basis": (o or {}).get("timing_basis"),
                     "prediction_validity": (o or {}).get("prediction_validity"), "has_admissible_prediction": admissible, "missing": comp["missing"], "warnings": comp["warnings"],
                     "complete": comp["complete"], "capture_status": cap, "capture_window": {"opens": h["window_start_utc"], "cutoff": h["cutoff_utc"]},
                     "snapshot_a": sa, "snapshot_b": sb, "snapshot_gap_days": pair.get("gap_days"), "eps_revision_pct": pair.get("eps_revision_pct"), "revenue_revision_pct": pair.get("revenue_revision_pct"),
                     "news_counts": (n or {}).get("counts") or {}, "news_freshest_valid_age_hours": (n or {}).get("freshest_valid_age_hours"), "news_review": (review_by_event or {}).get(h["event_id"]),
                     "evidence": evidence_classes(h, pair, n, o, admissible, has_pred, bool(bars_by_symbol.get(h["symbol"]))),
                     "signal_use": "NONE: missing, unverified or ineligible evidence is never a positive or negative trading signal"})
    acc = AC.report(validated, outc["events"])
    return {"schema": SCHEMA, "generated_at": datetime.now(UTC).isoformat(), "as_of": as_of.isoformat(), "source": source, "synthetic": synthetic, "notes": notes or [],
            "label": "SYNTHETIC EXAMPLE DATA - not market data" if synthetic else "Saved evidence - not live signals",
            "summary": {"events": len(rows), **PL.completeness_summary(comp_rows), "by_vendor_status": dict(Counter(r["vendor_status"] for r in rows)), "by_quarter_state": dict(Counter(r["quarter_state"] for r in rows)),
                        "by_pairing_state": dict(Counter(r["pairing_state"] for r in rows)), "evidence_classes": {k: dict(Counter(r["evidence"][k] for r in rows)) for k in ("calendar", "quarter", "snapshot_pair", "news", "price_bars", "prediction", "outcome")},
                        "by_capture_status": dict(Counter(r["capture_status"] for r in rows)), "news": news["summary"], "outcomes": outc["summary"], "predictions": {"total": len(validated), "admissible": sum(1 for v in validated if v["valid"]),
                        "reasons": dict(Counter(c for v in validated for c in v["reasons"]))}},
            "events": rows, "accuracy": acc}


def write(bundle: dict, path) -> Path:
    p = Path(path)
    if p.exists():
        raise FileExistsError(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bundle, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return p
