"""Two-page A4 PDF of the recovery audit (smart_move_recovery.build). Lists shrink to fit; text never does; the page count is verified after rendering."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, KeepInFrame, PageBreak, PageTemplate, Paragraph, Spacer

from smart_move_audit_pdf import GREY, H, P, TITLE, hdr, tbl
from smart_move_v2_interim_pdf import banner


def _counts(d):
    return ", ".join("%s %d" % (k.replace("_", " ").title() if k != "NO_TRADE" else "No trade", v) for k, v in sorted(d.items(), key=lambda kv: kv[0]))


def page1(r, lim, tests):
    gen = datetime.fromisoformat(r["generated_utc"])
    when = "%s UTC | %s Dubai | %s New York" % (gen.strftime("%Y-%m-%d %H:%M"), gen.astimezone(ZoneInfo("Asia/Dubai")).strftime("%H:%M"), gen.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M %Z"))
    ba, rc = r["before_after"], r["recovery"]
    s = [banner("RECOVERY AUDIT - NOTHING WAS MOVED FROM MISSING TO AVAILABLE WITHOUT A TIMESTAMP-VALID SOURCE"), Spacer(1, 2), Paragraph("TRDGO-US SMART MOVE V2 - MISSING-EVIDENCE RECOVERY", TITLE),
         P("Generated %s | %d stocks audited | audit hash %s | read-only: no provider request, no prediction rewritten. Every capture window has closed (events report 2 Nov), so only evidence that existed at capture time could be recovered." % (when, r["stocks"], r["audit_hash"][:16])),
         Paragraph("1. Before versus after", H)]
    s += [tbl([hdr(["", "Available /100", "Directional /100", "Continuation", "Contrarian"]),
               ["BEFORE", ba["before"]["available_avg"], ba["before"]["directional_avg"], _counts(ba["counts_before"]["continuation"]), _counts(ba["counts_before"]["contrarian"])],
               ["AFTER", ba["after"]["available_avg"], ba["after"]["directional_avg"], _counts(ba["counts_after"]["continuation"]), _counts(ba["counts_after"]["contrarian"])]], [16 * mm, 28 * mm, 30 * mm, 50 * mm, 50 * mm]),
          P("<b>Recovered factors: %d | stocks that gained valid evidence: %d | stocks improved: %d | stocks still blocked (DATA INCOMPLETE): %d of %d.</b> BUY and SELL remain 0 for both strategies; the 60-point minimum, margins and weights are unchanged." % (
              rc["recovered_factors"], rc["stocks_gaining_valid_evidence"], rc["stocks_improved"], rc["stocks_still_blocked"], r["stocks"]))]
    rows = [hdr(["Missing-evidence category", "Stocks", "Points missing"])]
    for k, v in sorted(r["missing_points_by_category"].items(), key=lambda kv: -kv[1]):
        rows.append([k.replace("_", " ").title(), r["stocks_by_category"].get(k, 0), "%g" % v])
    s += [Paragraph("2. Why evidence is missing (8 categories, all factors, all 95 stocks)", H), tbl(rows, [100 * mm, 22 * mm, 32 * mm])]
    s += [Paragraph("3. What the recovery pass tried", H)] + [P("- " + a) for a in rc["attempts"][:lim]]
    f = r["focus"]
    rows = [hdr(["Stock", "Directional", "Rule if covered", "Unresolved (points)", "Why it cannot be closed"])]
    for sym, x in f.items():
        hb = x["headroom_by_variant"]
        rows.append([sym, "%g /100" % x["directional"], P("Continuation: %s (bull %g / bear %g)<br/>Contrarian: %s (bull %g / bear %g)" % (hb["continuation"]["rule_would_fire_if_covered"] or "none", hb["continuation"]["bull"], hb["continuation"]["bear"],
                                                                                                                                        hb["contrarian"]["rule_would_fire_if_covered"] or "none", hb["contrarian"]["bull"], hb["contrarian"]["bear"])),
                     P(", ".join("%s %g" % (u["factor"], u["points"]) for u in x["unresolved"])), P("cutoff passed; flow: %s" % x["flow_state"].replace("_", " ").lower())])
    s += [Paragraph("4. VAL, CE and FANG (55 directional points each)", H), tbl(rows, [14 * mm, 20 * mm, 56 * mm, 46 * mm, 38 * mm]),
          P("Each would need 5 more directional points that existed BEFORE the cutoff (a qualifying flow, a 7-day consensus revision, or validated news). None did, so they stay DATA INCOMPLETE; they are not signals.")]
    return s


def page2(r, lim, tests):
    ch = r["consensus_history"]
    s = [banner("RECOVERY AUDIT - NOTHING WAS MOVED FROM MISSING TO AVAILABLE WITHOUT A TIMESTAMP-VALID SOURCE"), Spacer(1, 2), Paragraph("UNRESOLVED EVIDENCE, FIXES AND NEXT OBSERVATIONS", TITLE),
         Paragraph("5. Analyst consensus history found", H),
         P("%d stocks have saved point-in-time EPS consensus from 3 calendar observation clusters; longest span <b>%s h</b>; %d series changed (1-2 cents). Below the 7-day band: seen, never scored; nothing older is manufactured and today's consensus is never used as an older one. %s" % (
             ch["stocks"], ch["max_span_hours"], ch["series_with_a_change"], ch["note"]))]
    groups = defaultdict(list)
    for p in r["per_stock"]:
        sig = tuple(sorted((u["factor"], u["category"]) for u in p["unresolved"]))
        groups[sig].append(p["symbol"])
    rows = [hdr(["Stocks", "Unresolved factors (category number)", "Examples"])]
    for sig, syms in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:lim + 4]:
        rows.append([len(syms), P(", ".join("%s (%d)" % (f, c) for f, c in sig)), P(", ".join(syms[:6]) + (" ..." if len(syms) > 6 else ""))])
    s += [Paragraph("6. Per-stock unresolved factors, grouped (every stock is listed in the JSON)", H), tbl(rows, [16 * mm, 110 * mm, 48 * mm]),
          P("Categories: 1 mapping, 2 saved but unused, 3 genuine absence, 4 history unavailable, 5 unverified news, 6 stale calendar/capture time, 7 provider error, 8 implementation bug (none found).")]
    s += [Paragraph("7. Fixable now versus needs future observations", H),
          tbl([[P("<b>Fixable now</b>"), P(" | ".join(r["can_be_fixed_now"]))], [P("<b>Needs future observations</b>"), P(" | ".join(r["needs_future_observations"]))]], [36 * mm, 138 * mm], head=False)]
    s += [Paragraph("8. Verification", H), P(tests)]
    return s


def render(rec: dict, pdf_path, tests_line: str = "tests: see the commit") -> dict:
    pdf_path = Path(pdf_path)
    if pdf_path.exists():
        raise FileExistsError(pdf_path)
    W, Hh = A4
    m = 11 * mm
    fw, fh = W - 2 * m, Hh - 2 * m - 4 * mm
    last = None
    for lim in (4, 3, 2, 1):
        tmp = pdf_path.with_suffix(".tmp.pdf")
        doc = BaseDocTemplate(str(tmp), pagesize=A4, leftMargin=m, rightMargin=m, topMargin=m, bottomMargin=m + 4 * mm, title="Smart Move recovery audit")

        def footer(c, d, h=rec["audit_hash"]):
            c.saveState()
            c.setFont("Helvetica", 6.5)
            c.setFillColor(GREY)
            c.drawString(m, 9 * mm, "Recovery audit | audit hash %s | shadow research, not investment advice | page %d of 2" % (h[:32], d.page))
            c.restoreState()
        doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(m, m + 4 * mm, fw, fh, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=footer)])
        try:
            doc.build([KeepInFrame(fw, fh, page1(rec, lim, tests_line), mode="error"), PageBreak(), KeepInFrame(fw, fh, page2(rec, lim, tests_line), mode="error")])
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
    raise RuntimeError("recovery audit does not fit two pages: %s" % last)
