"""End-to-end check of the RUNNING local Smart Move app (API on 8791, Vite page on 5191): the numbers the page renders must equal what the API returns, and the API must equal the
saved evidence. Uses headless Chrome to render the real page and reads the final DOM. No provider request, no database.

    python e2e_check.py [--page http://127.0.0.1:5191] [--chrome <chrome.exe>]
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SM = ROOT / "docs" / "smart_move"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())


def dom(chrome, url):
    with tempfile.TemporaryDirectory() as prof:
        out = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--window-size=1600,1100", "--virtual-time-budget=20000", "--user-data-dir=" + prof, "--dump-dom", url],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    return out.stdout


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", default="http://127.0.0.1:5191")
    ap.add_argument("--chrome", default=CHROME)
    a = ap.parse_args()
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + name + ((" - " + detail) if detail and not ok else ""))

    # ---- API through the page's own proxy
    cal = get(a.page + "/api/smart-move/calendar?limit=1000")
    p1 = json.loads((SM / "p1p2/p1_report.json").read_text())
    replay2 = json.loads((SM / "shadow_impl/results/real_replay2/replay2.json").read_text())
    check("API calendar has 271 symbols", cal["counts"]["total"] == 271)
    vc = [r for r in cal["rows"] if r["calendar"]["status_at_observation"] == "VENDOR_CONFIRMED"]
    check("API VENDOR_CONFIRMED equals the saved P1 report (122)", len(vc) == p1["would_be_status_uw_symbols_for_2026_11_02"]["VENDOR_CONFIRMED"] == 122)
    check("API shows no BUY/SELL for current calendar", cal["summary"]["continuation"]["BUY"] == cal["summary"]["continuation"]["SELL"] == 0)
    sec = get(a.page + "/api/smart-move/replay?experiment=sec_moderate&limit=1000")
    s = replay2["experiments"]["B_moderate_adds_AMC_unconfirmed | valid_evidence_only"]["summary"]
    check("API SEC-moderate replay equals the saved replay summary", sec["summary"]["continuation"]["BUY"] == s["signals_continuation"]["BUY"] and sec["summary"]["contrarian"]["SELL"] == s["signals_contrarian"]["SELL"])
    prim = get(a.page + "/api/smart-move/replay?limit=1000")
    check("primary replay is the pre-registered source and all NO TRADE", prim["primary"] and not prim["exploratory"] and prim["summary"]["continuation"]["NO_TRADE"] == 186)

    # ---- the rendered page
    h = dom(a.chrome, a.page + "/smart-move")
    t = text(h)
    check("page: title, not-live banner and engine hash", "Smart Move" in t and "Not live signals" in t and "b25bd8d78894" in t)
    check("page: 271 rows rendered", len(re.findall(r"<tr[^>]*tabindex", h)) == 271, str(len(re.findall(r"<tr[^>]*tabindex", h))))
    check("page: 122 VENDOR CONFIRMED badges", t.count("VENDOR CONFIRMED") >= 122, str(t.count("VENDOR CONFIRMED")))
    check("page: shows 271 NO TRADE for both variants and zero BUY/SELL", len(re.findall(r"271 NO TRADE", t)) >= 2 and "0 BUY" in t and "0 SELL" in t)
    check("page: freshness line shows the saved time and age", "Current-calendar evidence saved" in t and re.search(r"\d+(\.\d+)? h old", t) is not None)
    check("page: missing evidence chips shown", "F3" in t and "F7" in t)

    h2 = dom(a.chrome, a.page + "/smart-move?set=replay&exp=sec_moderate")
    t2 = text(h2)
    check("page: replay labelled historical, exploratory experiment flagged", "Not a live prediction" in t2 and "EXPLORATORY" in t2 and "HINDSIGHT NOT VERIFIABLE" in t2)
    check("page: replay summary equals the saved summary (%d BUY / %d SELL continuation)" % (s["signals_continuation"]["BUY"], s["signals_continuation"]["SELL"]),
          ("%d BUY" % s["signals_continuation"]["BUY"]) in t2 and ("%d SELL" % s["signals_continuation"]["SELL"]) in t2 and ("%d NO TRADE" % s["signals_continuation"]["NO_TRADE"]) in t2)

    h3 = dom(a.chrome, a.page + "/smart-move?set=replay")
    t3 = text(h3)
    check("page: primary replay shows 186 NO TRADE, the PRIMARY badge and no exploratory badge", "186 NO TRADE" in t3 and "PRIMARY" in t3 and "pre-registered source" in t3 and re.search(r'class="badge amber"[^>]*>(?:<[^>]+>)*\s*EXPLORATORY', h3) is None)

    h4 = dom(a.chrome, a.page + "/smart-move?set=synthetic")
    t4 = text(h4)
    syn = json.loads((SM / "shadow_impl/results/synthetic_season_results.json").read_text())
    check("page: synthetic labelled and 28 rows", "invented" in t4.lower() and "Not real" in t4 and len(re.findall(r"<tr[^>]*tabindex", h4)) == 28)
    check("page: synthetic BUY count equals the saved file", ("%d BUY" % syn["signals"]["continuation"]["BUY"]) in t4)

    h5 = dom(a.chrome, a.page + "/smart-move?symbol=AAPL")
    t5 = text(h5)
    aapl = get(a.page + "/api/smart-move/stock/AAPL")["current_calendar"][0]
    check("page: AAPL detail shows vendors, flag, engine output and missing evidence",
          "Current calendar observation" in t5 and "UW_CALENDAR" in t5 and "FINNHUB" in t5 and "company" in t5 and "NO_PRICE_EVIDENCE_SAVED" in t5 and ("available %d/40" % aapl["available_points"]) in t5)
    h6 = dom(a.chrome, a.page + "/smart-move?symbol=ZZZZ")
    check("page: unknown symbol shows an error state, not data", "No saved data for this symbol" in text(h6))

    bad = [n for n, ok, _ in results if not ok]
    print("\n%d checks, %d failed" % (len(results), len(bad)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
