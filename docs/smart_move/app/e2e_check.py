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
    ap.add_argument("--sim-page", default="", help="page URL of a server started with SMART_MOVE_MODE=SIMULATION (optional live-path checks)")
    a = ap.parse_args()
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + name + ((" - " + detail) if detail and not ok else ""))

    if a.sim_page:
        sim = a.sim_page
        import time as _t
        for _ in range(4):  # make the simulated store fresh right before the fresh-state checks (the same governed manual refresh a user would press; one per minute at most)
            try:
                urllib.request.urlopen(urllib.request.Request(sim + "/api/smart-move/refresh", data=b"{}", headers={"Content-Type": "application/json"}, method="POST"), timeout=120).read()
            except Exception:  # noqa: BLE001
                pass
            lv = get(sim + "/api/smart-move/live?limit=1")
            if get(sim + "/api/smart-move/feed")["freshness"]["calendar"]["state"] == "FRESH" and lv["summary"]["available_points_distribution"] == {"40": 16}:
                break
            _t.sleep(61)
        live = get(sim + "/api/smart-move/live?limit=1000")
        check("SIM API: 16 live rows scored from 40/40 evidence with BUY and SELL present", live["counts"]["total"] == 16 and live["summary"]["available_points_distribution"] == {"40": 16} and live["summary"]["continuation"]["BUY"] > 0 and live["summary"]["continuation"]["SELL"] > 0)
        check("SIM API: every row is labelled SIMULATION and every signal is a frozen-engine output", all(r["evidence_mode"] == "SIMULATION" for r in live["rows"]) and all(r["engine"][v]["signal"] == r["engine"][v]["score_before_gate"] or r["engine"][v]["signal"] == "NO_TRADE" for r in live["rows"] for v in ("continuation", "contrarian")))
        dom(a.chrome, sim + "/smart-move")        # warm-up: the first cold load of the dev server compiles the page and can exceed the render budget
        hs = dom(a.chrome, sim + "/smart-move")
        ts = text(hs)
        c = live["summary"]["continuation"]
        parts = {"banner": "SIMULATED PROVIDER" in ts, "feed OK": re.search(r"\bOK\b", ts) is not None, "authorized": "of 1700 authorized" in ts, "last updated": "Last updated" in ts, "next refresh": "Next refresh" in ts}
        check("SIM page: banner, feed OK, requests of authorized, last updated, next refresh", all(parts.values()), json.dumps(parts) + " | " + ts[ts.find("Feed status"):ts.find("Feed status") + 120])
        check("SIM page: 16 rows and the continuation counts equal the API (%d BUY / %d SELL)" % (c["BUY"], c["SELL"]), len(re.findall(r"<tr[^>]*tabindex", hs)) == 16 and ("%d BUY" % c["BUY"]) in ts and ("%d SELL" % c["SELL"]) in ts)
        hd = dom(a.chrome, sim + "/smart-move?symbol=SIM00")
        td = text(hd)
        check("SIM page: detail shows live evidence, retrieval times, hashes and the separate SEC-F3 note", "Live current evidence" in td and "SIMULATED PROVIDER · not real" in td and "Evidence used" in td and "SEC_EVIDENCE_NOT_COLLECTED" in td and "separate" in td)
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
    h = dom(a.chrome, a.page + "/smart-move?set=calendar")
    t = text(h)
    check("page: title, not-live banner and engine hash (saved calendar tab)", "Smart Move" in t and "Not live signals" in t and "b25bd8d78894" in t)
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

    # ---- default server: live tab must show the exact blockers, no signals, and a refresh that is refused
    feed0 = get(a.page + "/api/smart-move/feed")
    check("default feed: kill switch engaged, authorization 0, nothing sent", feed0["status"] == "DISABLED_KILL_SWITCH" and feed0["authorized_daily"] == 0 and feed0["requests_today"] == 0 and feed0["next_refresh"] is None)
    h7 = dom(a.chrome, a.page + "/smart-move")
    t7 = text(h7)
    check("page: live tab shows Feed status, Last updated (never), Next refresh (not scheduled)", "DISABLED KILL SWITCH" in t7 and "never" in t7 and "not scheduled" in t7)
    check("page: live tab lists the exact blockers", "Why there are no live signals" in t7 and "KILL_SWITCH" in t7 and "NOT_AUTHORIZED" in t7 and "NO_LIVE_EVIDENCE" in t7 and "No live evidence collected yet" in t7)
    check("page: refresh and kill buttons are disabled while blocked", len(re.findall(r"<button[^>]*disabled[^>]*>(?:<[^>]+>)*\s*(?:Refresh now|Kill switch)", h7)) >= 1 or ("Refresh now" in h7 and "disabled" in h7))
    req = urllib.request.Request(a.page + "/api/smart-move/refresh", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=30)
        refused = False
    except urllib.error.HTTPError as e:
        refused = e.code == 403 and json.loads(e.read())["detail"]["requests_sent"] == 0
    check("default server refuses a manual refresh (403) and still has sent 0 requests", refused and get(a.page + "/api/smart-move/feed")["requests_today"] == 0)

    if a.sim_page:
        sim = a.sim_page
        import time
        deadline = time.time() + 480
        while time.time() < deadline and get(sim + "/api/smart-move/feed")["freshness"]["calendar"]["state"] != "STALE":
            time.sleep(10)
        fs = get(sim + "/api/smart-move/feed")
        check("SIM feed: the calendar turns STALE and the status says so", fs["freshness"]["calendar"]["state"] == "STALE" and fs["status"] == "STALE")
        ls = get(sim + "/api/smart-move/live?limit=1000")
        check("SIM API: stale calendar blocks every signal (no BUY/SELL) and explains why", ls["summary"]["continuation"]["BUY"] == ls["summary"]["continuation"]["SELL"] == 0 and any(b["code"] == "STALE_CALENDAR" for b in ls["blockers"]) and all(r["blocked_by"] for r in ls["rows"]))
        hst = dom(a.chrome, sim + "/smart-move")
        tst = text(hst)
        check("SIM page: stale warning, STALE status and blocked rows are shown", "Why signals may be blocked" in tst and "STALE_CALENDAR" in tst and "calendar stale" in tst.lower() and "0 BUY" in tst)

    bad = [n for n, ok, _ in results if not ok]
    print("\n%d checks, %d failed" % (len(results), len(bad)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
