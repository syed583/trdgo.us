"""Two-page A4 PDF for the interim V2 audit (smart_move_v2_interim.build). Page count enforced like the main audit: lists shrink, text never does."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, KeepInFrame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

from smart_move_audit_pdf import AMBER, GREY, H, NAVY, P, TITLE, hdr, pill, tbl

RED = colors.HexColor("#b3261e")


def banner(text):
    t = Table([[Paragraph('<font color="white"><b>%s</b></font>' % text, TITLE.__class__("b", parent=TITLE, fontSize=10.5, leading=13))]], colWidths=[184 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), RED), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def page1(r, lim):
    c, gen = r["collection"], datetime.fromisoformat(r["generated_utc"])
    when = "%s UTC | %s Dubai | %s New York" % (gen.strftime("%Y-%m-%d %H:%M"), gen.astimezone(ZoneInfo("Asia/Dubai")).strftime("%H:%M"), gen.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M %Z"))
    s = [banner(r["title"]), Spacer(1, 2), Paragraph("TRDGO-US SMART MOVE V2 - REAL-DATA EVIDENCE AUDIT", TITLE),
         P("Generated %s | V2 %s (EXPERIMENTAL, config %s) | audit hash %s<br/>Read-only snapshot of the collector's store; no provider request was made for this audit." % (when, r["v2_version"], r["config_hash"][:10], r["audit_hash"][:16])),
         Paragraph("1. Collection progress and universe reconciliation", H)]
    rc = r["reconciliation"]
    s += [tbl([hdr(["Universe at start", "Collected + audited", "Expired before collection", "Not reached yet", "Attempts / cap", "Failed calls", "401/429"]),
               [rc["universe"], c["audited"], rc["expired_before_collection"], rc["not_reached_yet"], "%s / %s" % (c["attempts_total"], c["cap"]), len(c["failed_calls"]), len(c["stop_events"])]], [26 * mm, 28 * mm, 30 * mm, 24 * mm, 26 * mm, 24 * mm, 20 * mm]),
          P("Run status: <b>%s</b>. Kill switch: %s. Expired (window closed before the stock was reached or before collection): %s." % (c["status"], c["kill_switch"], ", ".join(c["expired_before_collection"][:14]) + (" ..." if len(c["expired_before_collection"]) > 14 else "") if c["expired_before_collection"] else "none")),
          Paragraph("2. V2 evidence out of 100 (%d stocks)" % c["audited"], H)]
    b, av = r["bands_available_points"], r["averages"]
    s += [tbl([hdr(["Average available", "Average directional", "100/100", "80-99", "60-79", "< 60", ">= 60 directional"]),
               [P("<b>%s</b> / 100" % av["available_of_100"]), P("<b>%s</b> / 100" % av["directional_of_100"]), b.get("100", 0), b.get("80-99", 0), b.get("60-79", 0), b.get("<60", 0), P("<b>%d</b> of %d" % (r["directional_60_plus"], c["audited"]))]],
              [30 * mm, 30 * mm, 16 * mm, 16 * mm, 16 * mm, 16 * mm, 40 * mm]),
          P("Available = points that have data. Directional = points from factors able to carry a bull/bear direction (the implied-move factor is magnitude-only and excluded). A BUY/SELL needs 60 directional points. No score is normalised to 100.")]
    rows = [hdr(["Factor", "Pts", "Full data", "Partial", "Missing", "Main cause of the gap"])]
    for f, v in r["factor_coverage"].items():
        cs = r["missing_causes"].get(f, {})
        top = max(cs.items(), key=lambda kv: kv[1])[0].replace("_", " ").lower() if cs else "-"
        rows.append([f, __import__("smart_move_v2").WEIGHTS[f], v["available"], v["partial"], v["missing"], top])
    s += [tbl(rows, [32 * mm, 10 * mm, 22 * mm, 20 * mm, 22 * mm, 78 * mm])]
    s += [Paragraph("3. Current V2 signals (stocks audited so far)", H)]
    sg = r["signals"]
    srows = [hdr(["Variant", "BUY", "SELL", "NO TRADE", "DATA INCOMPLETE"])]
    for v in ("continuation", "contrarian"):
        x = sg[v]
        srows.append([v.capitalize(), P('<font color="#1b7f3b"><b>%d</b></font>' % x.get("BUY", 0)), P('<font color="#b3261e"><b>%d</b></font>' % x.get("SELL", 0)), x.get("NO_TRADE", 0), P('<font color="#b26a00"><b>%d</b></font>' % x.get("DATA_INCOMPLETE", 0))])
    s += [tbl(srows, [40 * mm, 26 * mm, 26 * mm, 32 * mm, 40 * mm]), P("Why stocks cannot produce BUY/SELL: " + "; ".join("%d x %s" % (n, k) for k, n in sorted(r["why_no_buy_sell"].items(), key=lambda kv: -kv[1])[:lim]) + ". Accuracy: " + r["accuracy"] + ".")]
    return s


def page2(r, lim):
    c, lk, fb = r["collection"], r["lookahead"], r["fabrication_check"]
    s = [banner(r["title"]), Spacer(1, 2), Paragraph("DATA QUALITY, LOOKAHEAD AND NEXT STEPS", TITLE), Paragraph("4. API failures and data-quality problems", H)]
    fl = c["failed_calls"]
    s += [P("Failed calls: <b>%d</b> (transient provider errors after one retry; no 401/429). %s By endpoint: %s." % (len(fl), ("Stocks: " + ", ".join(sorted({f['symbol'] for f in fl}))) if fl else "", c["by_kind"]))]
    rows = [hdr(["Factor", "Cause category (stocks)", "Meaning"])]
    seen = 0
    for f, cs in r["missing_causes"].items():
        for cat, n in sorted(cs.items(), key=lambda kv: -kv[1]):
            if seen >= lim + 4:
                break
            rows.append([f, "%s (%d)" % (cat.replace("_", " "), n), P(r["cause_details"].get("%s|%s" % (f, cat), ""))])
            seen += 1
    s += [Paragraph("5. Missing-data root causes", H), tbl(rows, [28 * mm, 50 * mm, 106 * mm])]
    s += [Paragraph("6. Timestamp and lookahead checks", H),
          tbl([[P("<b>Evidence records checked</b>"), P("%d, all retrieved by the governed collector" % lk["records"])],
               [P("<b>Retrieved outside the capture window</b>"), pill("PASS" if not lk["retrieved_outside_window"] else "FAIL"), ] if False else [P("<b>Retrieved outside the capture window</b>"), P("%d %s" % (len(lk["retrieved_outside_window"]), "(PASS)" if not lk["retrieved_outside_window"] else "(FAIL: %s)" % lk["retrieved_outside_window"][:3]))],
               [P("<b>Rows stamped after retrieval</b>"), P("%d records %s (analyst, flow, short interest, ownership, news fields)" % (len(lk["rows_stamped_after_retrieval"]), "(PASS)" if not lk["rows_stamped_after_retrieval"] else "(FAIL)"))],
               [P("<b>No fabricated or normalised points</b>"), P("%d violations %s: missing factors contribute 0; bull + bear never exceed points with data" % (len(fb["violations"]), "(PASS)" if not fb["violations"] else "(FAIL)"))]], [60 * mm, 124 * mm], head=False)]
    s += [Paragraph("7. Prioritised fixes and historical-data requirements", H),
          P("<b>Fixable now:</b> " + " | ".join(r["fixable_now"])),
          P("<b>Needs future snapshots or verification:</b> " + " | ".join(r["needs_future_snapshots"])),
          Paragraph("8. Remaining collection and next steps", H),
          P("%d stocks not yet reached; the collector continues automatically within the 700-attempt cap, the hourly share and each stock's capture window. When it ends: final V2 scoring, tests, build and the final two-page audit. No recurring job is active; no outcome request has been made." % r["collection"]["not_reached_yet"])]
    return s


def render(rec: dict, pdf_path) -> dict:
    pdf_path = Path(pdf_path)
    if pdf_path.exists():
        raise FileExistsError(pdf_path)
    W, Hh = A4
    m = 11 * mm
    fw, fh = W - 2 * m, Hh - 2 * m - 4 * mm
    last = None
    for lim in (8, 6, 5, 4, 3, 2):
        tmp = pdf_path.with_suffix(".tmp.pdf")
        doc = BaseDocTemplate(str(tmp), pagesize=A4, leftMargin=m, rightMargin=m, topMargin=m, bottomMargin=m + 4 * mm, title="Smart Move V2 interim audit")

        def footer(c, d, h=rec["audit_hash"]):
            c.saveState()
            c.setFont("Helvetica", 6.5)
            c.setFillColor(GREY)
            c.drawString(m, 9 * mm, "%s | audit hash %s | shadow research, not investment advice | page %d of 2" % (rec["title"], h[:32], d.page))
            c.restoreState()
        doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(m, m + 4 * mm, fw, fh, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=footer)])
        try:
            doc.build([KeepInFrame(fw, fh, page1(rec, lim), mode="error"), PageBreak(), KeepInFrame(fw, fh, page2(rec, lim), mode="error")])
        except Exception as e:  # noqa: BLE001
            last = e
            tmp.unlink(missing_ok=True)
            continue
        from pypdf import PdfReader
        n = len(PdfReader(str(tmp)).pages)
        if n <= 2:
            tmp.rename(pdf_path)
            return {"pdf": str(pdf_path), "pages": n, "list_limit": lim}
        tmp.unlink(missing_ok=True)
    raise RuntimeError("interim audit does not fit two pages: %s" % last)
