"""Renders the audit record (smart_move_audit.build) as a TWO-PAGE A4 PDF. The page count is enforced: each page lives in a frame that raises on overflow, and the renderer
retries with shorter lists (never smaller text) until both pages fit; the finished file is re-read and its page count checked, and a longer file is deleted and refused."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, KeepInFrame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.platypus.doctemplate import LayoutError

GREEN, RED, AMBER, GREY, NAVY, INK = colors.HexColor("#1b7f3b"), colors.HexColor("#b3261e"), colors.HexColor("#b26a00"), colors.HexColor("#6b7280"), colors.HexColor("#14213d"), colors.HexColor("#111827")
LIGHT = colors.HexColor("#f3f4f6")
BODY = ParagraphStyle("b", fontName="Helvetica", fontSize=8.5, leading=10.5, textColor=INK)
SMALL = ParagraphStyle("s", parent=BODY, fontSize=8.2, leading=10)
H = ParagraphStyle("h", parent=BODY, fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=NAVY, spaceBefore=3, spaceAfter=1.5)
TITLE = ParagraphStyle("t", parent=BODY, fontName="Helvetica-Bold", fontSize=12.5, leading=15, textColor=NAVY)
MAX_PAGES = 2


def P(t, st=SMALL):
    """Allows the few tags used here (<b>, <br/>, <font>); a bare '<' or '&' in data is escaped."""
    import re
    t = re.sub(r"&(?!(amp|lt|gt|#\d+);)", "&amp;", str(t))
    return Paragraph(re.sub(r"<(?![a-zA-Z/])", "&lt;", t), st)


def tone(word):
    w = str(word).upper()
    if w.startswith(("PASS", "READY", "ENGAGED", "OK", "BUY", "VERIFIED")):
        return GREEN
    if w.startswith(("BLOCKED", "FAIL", "SELL", "CRITICAL", "HIGH", "NOT ENGAGED")):
        return RED
    if w.startswith(("PARTIAL", "MED", "UNAVAILABLE", "EXPERIMENTAL", "INCONCLUSIVE", "NOT VERIFIED", "NOT AVAILABLE", "DATA")):
        return AMBER
    return GREY


def pill(word):
    return Paragraph('<font color="%s"><b>%s</b></font>' % (tone(word).hexval().replace("0x", "#"), str(word).replace("&", "&amp;").replace("<", "&lt;")), SMALL)


def tbl(data, widths, head=True, zebra=True):
    t = Table(data, colWidths=widths, repeatRows=1 if head else 0)
    st = [("FONTSIZE", (0, 0), (-1, -1), 8.2), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db"))]
    if head:
        st += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
    if zebra:
        st += [("BACKGROUND", (0, i), (-1, i), LIGHT) for i in range(2, len(data), 2)]
    t.setStyle(TableStyle(st))
    return t


def hdr(cells):
    return [Paragraph('<font color="white"><b>%s</b></font>' % c, SMALL) for c in cells]


def page1(r, lim):
    ev, sg = r["events"], r["signals"]
    gen = datetime.fromisoformat(r["generated_utc"])
    when = "%s UTC | %s Dubai (GST) | %s New York (%s)" % (gen.strftime("%Y-%m-%d %H:%M"), gen.astimezone(ZoneInfo("Asia/Dubai")).strftime("%H:%M"), gen.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M"), gen.astimezone(ZoneInfo("America/New_York")).strftime("%Z"))
    s = [Paragraph("TRDGO-US SMART MOVE - EARNINGS PREDICTION AUDIT", TITLE),
         P("Generated %s<br/>Run <b>%s</b> | data: <b>%s</b> | evidence timestamp %s | audit hash %s" % (when, r["run_id"], r["mode"], r["evidence_timestamp_utc"] or NA, r["audit_hash"][:16]), SMALL),
         Spacer(1, 3)]
    d = r["event"]["dates"]
    s += [Paragraph("1. Event and models", H),
          tbl([hdr(["Earnings event", "Timing", "Model", "Points", "Status"]),
               [", ".join(d), "AMC %d / BMO %d" % (r["event"]["timing"].get("AMC", 0), r["event"]["timing"].get("BMO", 0)), "V1 (frozen)", "40", pill("READY - shadow research, no accuracy claim")],
               ["", "", "V2 (experimental)", "100", pill("UNAVAILABLE - %s" % r["models"]["V2"]["status"])]], [38 * mm, 30 * mm, 30 * mm, 14 * mm, 70 * mm])]
    s += [Paragraph("2. Universe", H),
          tbl([hdr(["Calendar", "Eligible", "Evaluated", "Pending", "Incomplete", "Excluded (removed)"]),
               [ev["calendar_events"], ev["eligible"], ev["evaluated"], ev["pending"], ev["incomplete"], "%d %s" % (ev["excluded_removed"], ", ".join(x["symbol"] for x in ev["removed"]))]], [24 * mm, 24 * mm, 24 * mm, 24 * mm, 28 * mm, 58 * mm]),
          Paragraph("3. Signals (V1; Continuation and Contrarian are scored separately)", H)]
    rows = [hdr(["Variant", "BUY", "SELL", "NO TRADE", "DATA INCOMPLETE", "PENDING"])]
    for v in ("continuation", "contrarian"):
        c = sg[v]
        rows.append([v.capitalize(), P('<font color="#1b7f3b"><b>%d</b></font>' % c.get("BUY", 0)), P('<font color="#b3261e"><b>%d</b></font>' % c.get("SELL", 0)), P('<b>%d</b>' % c.get("NO_TRADE", 0)),
                     P('<font color="#b26a00"><b>%d</b></font>' % c.get("DATA_INCOMPLETE", 0)), c.get("PENDING", 0)])
    v2s = r.get("signals_v2")
    if v2s:
        for v in ("continuation", "contrarian"):
            c = v2s[v]
            rows.append(["V2 " + v.capitalize() + " (exp.)", c.get("BUY", 0), c.get("SELL", 0), c.get("NO_TRADE", 0), P('<font color="#b26a00"><b>%d</b></font>' % c.get("DATA_INCOMPLETE", 0)), c.get("PENDING", 0)])
    cov = r["evidence_coverage"]
    s += [tbl(rows, [40 * mm, 20 * mm, 20 * mm, 26 * mm, 36 * mm, 22 * mm]),
          P("Evidence coverage: " + " | ".join("%s: %d" % (k, v) for k, v in cov.items()) + ". Missing data is shown as DATA INCOMPLETE, never as a neutral or negative signal.")]
    oc = r["outcomes"]
    s += [P("Actual outcomes: <b>%d graded</b>. %s" % (oc["graded"], oc["accuracy"] if not oc["graded"] else "Descriptive hit rates are on page 2; not enough decided events for any accuracy claim."))]
    s += [Paragraph("4. Top predictions (highest bullish/bearish points; engine rule met, no gate)", H)]
    tops = r["top_signals"][:lim]
    s += [tbl([hdr(["Symbol", "Variant", "Signal", "Points", "Evidence", "Reason"])] +
              [[t["symbol"], t["variant"], P('<font color="%s"><b>%s</b></font>' % ("#1b7f3b" if t["signal"] == "BUY" else "#b3261e", t["signal"])), "%s pts" % t["score"], t["evidence"], P("%s (V1 rule met, no gate)" % t["reason"])] for t in tops],
              [20 * mm, 26 * mm, 18 * mm, 18 * mm, 20 * mm, 72 * mm])]
    dg = r["disagreements"]
    s += [P("Model disagreements (Continuation BUY vs Contrarian SELL or the reverse): <b>%d</b> %s" % (dg["count"], ("- " + ", ".join(dg["symbols"][:lim * 2])) if dg["symbols"] else ""))]
    sd = (r["v2"] or {}).get("signal_differences")
    if sd:
        s += [P("V1 (original, Oct 31 capture) to V2 (new capture) signal changes: " + " | ".join("%s: %s" % (v, ", ".join("%s %d" % (k.replace("None", "none"), n) for k, n in sorted(c.items()))) for v, c in sd["v1_original_vs_v2"].items()) + ("; opposite directions: %s" % ", ".join(sd["opposite"][:6]) if sd["opposite"] else "; no opposite BUY/SELL pairs") + ".")]
    cl = (r["v2"] or {}).get("collection")
    pv = r["provider"]
    s += [Paragraph("5. Provider freshness and readiness", H),
          P((("V2 collection (one-time, real provider): <b>%d stocks</b>, %s attempts of cap %s, run %s, last collection <b>%s</b>, kill switch %s.<br/>" % (cl["stocks_collected"], cl["attempts_total"], cl["cap"], cl["status"], cl["last_collection_utc"] or NA, "engaged" if cl["kill_switch_final"] else "NOT ENGAGED")) if cl else "") +
            "Latest evidence retrieved: <b>%s</b>. V1 run: %s (%s), %s attempts of cap %s (%s%%), %d errors, %d 401/429 stops." %
            (pv["latest_evidence_retrieved_utc"] or NA, pv["run_updated_utc"] or NA, pv["run_status"], pv["attempts_total"], pv["cap"], pv["budget_utilisation_pct"], pv["errors"], pv["stop_events_401_429"])),
          Spacer(1, 2), tbl([[pill(r["verdict"]), P("<b>Readiness: %s.</b> %s" % (r["verdict"], " ".join("(%d) %s." % (i + 1, x.rstrip(".")) for i, x in enumerate(r["verdict_reasons"][:3]))))]], [20 * mm, 150 * mm], head=False, zebra=False),
          P("Shadow research only. Not investment advice. No broker connection or order is possible from this system.")]
    return s


def page2(r, lim):
    v2, pv, ops = r["v2"], r["provider"], r["operations"]
    cl = v2.get("collection")
    s = [Paragraph("TECHNICAL VALIDATION AND PROBLEM AUDIT", TITLE), Spacer(1, 2), Paragraph("1. V2 factor coverage (all 10 parameters)", H)]
    rows = [hdr(["Factor", "Pts", "Stocks with data", "Data", "Source / endpoint", "Note"])]
    for f in v2["factors"]:
        have = "%s / %s" % (f["stocks_with_data"], f["stocks"]) if f["stocks"] is not None else NA
        rows.append([f["factor"], f["points"], have, pill(f["data_status"]), P(f["source"]), P(f["note"])])
    s += [tbl(rows, [31 * mm, 8 * mm, 21 * mm, 18 * mm, 50 * mm, 46 * mm]),
          P("V2: at most %s of 100 points have data, of which %s are DIRECTIONAL (60 required; the implied-move factor is magnitude-only and does not count). Every V2 result is DATA INCOMPLETE. Missing is not negative." % (("%g" % v2["available_points_max"]) if v2.get("available_points_max") is not None else NA, ("%g" % v2["directional_available_max"]) if v2.get("directional_available_max") is not None else NA))]
    s += [Paragraph("2. Provider requests, capture windows, liquidity", H)]
    w, lq = r["windows"], r["liquidity"]
    s += [tbl([hdr(["Item", "Value"]),
               ["Attempts / by provider", "%s attempts (%s) | errors %d | 401/429 %d | budget %s%% of cap %s" % (pv["attempts_total"], ", ".join("%s %s" % kv for kv in (pv["by_provider"] or {}).items()), pv["errors"], pv["stop_events_401_429"], pv["budget_utilisation_pct"], pv["cap"])],
               ] + ([["V2 collection", P("%d stocks | attempts %s of %s | by kind %s | probe %s endpoints: %s | failures %d | stop events %d | open-window events %s, window closed %d, expired before collection %d" % (
                   (cl or {}).get("stocks_collected", 0), (cl or {}).get("attempts_total"), (cl or {}).get("cap"), (cl or {}).get("by_kind"), (cl or {}).get("probe_symbol"),
                   "; ".join("%s %s" % kv for kv in ((cl or {}).get("endpoint_access") or {}).items()), (cl or {}).get("failures", 0), len((cl or {}).get("stop_events") or []),
                   (cl or {}).get("open_window_events"), len((cl or {}).get("window_closed") or []), len((cl or {}).get("expired_before_collection") or [])))]] if cl else []) + [
               ["Subscription / limits", P(pv["subscription"] + ". " + pv["limits"])],
               ["Capture windows", P("evaluated %d | open, no prediction %d | closed and missed %d | not yet open %d" % (w["evaluated"], w["open_without_prediction"], w["late_or_expired"], w["ahead"]))],
               ["Liquidity / entry (V1)", P(" | ".join("%s: %d" % (k, v) for k, v in lq.items()))]], [36 * mm, 138 * mm])]
    o, cmpv = r["outcomes"], r["v1_vs_v2"]
    s += [Paragraph("3. Outcomes, accuracy, V1 versus V2", H),
          tbl([[P("<b>Outcomes graded</b>"), P("%d. Accuracy: %s" % (o["graded"], o["accuracy"]))], [P("<b>V1 vs V2</b>"), P(cmpv["verdict"])]] + ([[P("<b>V1 descriptive</b>"), P(" | ".join("%s: %d decided, hit %s, 95%% CI %s (BUY %d/%d, SELL %d/%d)" % (v, x["decided"], x["hit_rate"], x["ci95"], x["buy"]["hits"], x["buy"]["n"], x["sell"]["hits"], x["sell"]["n"]) for v, x in o["v1_descriptive"].items()))]] if o.get("v1_descriptive") else []), [36 * mm, 138 * mm], head=False)]
    t = r["tests"]
    bk = ops["backup"]
    bks = "VERIFIED: %s (%d files)" % (bk["folder"], bk["files"]) if isinstance(bk, dict) and bk.get("ok") else str(bk)
    s += [Paragraph("4. Operations and tests", H),
          tbl([[P("<b>Scheduler</b>"), P(ops["scheduler"] + "; est. %s requests/day for this universe" % ops["estimated_daily_requests"])],
               [P("<b>Kill switch</b>"), pill(ops["kill_switch"])], [P("<b>Health checks</b>"), pill("PASS" if ops["health_ok"] else "FAIL")], [P("<b>Backup</b>"), P(bks)],
               [P("<b>Tests</b>"), P(t.get("summary", t.get("recorded", NV)) if isinstance(t, dict) else NV)]], [36 * mm, 138 * mm], head=False)]
    s += [Paragraph("5. Defects and required actions", H)]
    d = r["defects"][:lim]
    s += [tbl([hdr(["Sev.", "Component", "Defect", "Required action"])] + [[pill(x["severity"]), x["component"], P(x["defect"]), P(x["action"])] for x in d], [12 * mm, 20 * mm, 76 * mm, 66 * mm])]
    if len(r["defects"]) > len(d):
        s += [P("<b>+%d further item(s) not shown here (lower severity, listed in the JSON audit record):</b> %s" % (len(r["defects"]) - len(d), "; ".join("%s: %s" % (x["component"], x["defect"][:70]) for x in r["defects"][lim:lim + 3])))]
    s += [Spacer(1, 3), tbl([[pill(r["verdict"]), P("<b>Final readiness verdict: %s</b> - live activation is not approved. Reasons: %s" % (r["verdict"], "; ".join(x.rstrip(".") for x in r["verdict_reasons"][:3])))]], [20 * mm, 154 * mm], head=False, zebra=False)]
    return s


NA, NV = "NOT AVAILABLE", "NOT VERIFIED"


def render(rec: dict, pdf_path) -> dict:
    pdf_path = Path(pdf_path)
    if pdf_path.exists():
        raise FileExistsError("PDF exists; never overwritten: %s" % pdf_path)
    W, Hh = A4
    m = 11 * mm
    last = None
    for lim in (8, 6, 5, 4, 3, 2):
        tmp = pdf_path.with_suffix(".tmp.pdf")
        doc = BaseDocTemplate(str(tmp), pagesize=A4, leftMargin=m, rightMargin=m, topMargin=m, bottomMargin=m + 4 * mm, title="TRDGO-US Smart Move - Earnings Prediction Audit",
                              author="TRDGO-US Smart Move", subject="run %s hash %s" % (rec["run_id"], rec["audit_hash"]))
        fw, fh = W - 2 * m, Hh - 2 * m - 4 * mm

        def footer(c, d, rid=rec["run_id"], h=rec["audit_hash"]):
            c.saveState()
            c.setFont("Helvetica", 6.5)
            c.setFillColor(GREY)
            c.drawString(m, 9 * mm, "Run %s | audit hash %s | shadow research, not investment advice | page %d of 2" % (rid, h[:32], d.page))
            c.restoreState()
        doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(m, m + 4 * mm, fw, fh, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=footer)])
        story = [KeepInFrame(fw, fh, page1(rec, min(8, lim + 3)), mode="error"), PageBreak(), KeepInFrame(fw, fh, page2(rec, lim), mode="error")]
        try:
            doc.build(story)
        except (LayoutError, Exception) as e:       # noqa: BLE001  overflow -> shorter lists, never smaller text
            last = e
            tmp.unlink(missing_ok=True)
            continue
        from pypdf import PdfReader
        n = len(PdfReader(str(tmp)).pages)
        if n <= MAX_PAGES:
            tmp.rename(pdf_path)
            return {"pdf": str(pdf_path), "pages": n, "list_limit": lim}
        tmp.unlink(missing_ok=True)
    raise RuntimeError("audit does not fit two pages: %s" % last)
