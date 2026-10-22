"""Verify the All-in-One sheet against each model's own tab (read-only).

Run on the server (or locally):  python verify_all_models.py [N_TICKERS]

Checks (1) every sheet column header equals the sidebar label of the tab it maps to,
(2) for N tickers that every model has scored, each sheet cell (signal, score,
coverage) equals that model's tab scan row exactly. Exit code 1 on any mismatch.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import all_models_service as am  # noqa: E402

ROUTES = {"claude": "/claude-last", "claude_pro": "/claude-pro", "gemini": "/gemini", "grok": "/grok",
          "chatgpt": "/chatgpt", "meta": "/meta", "kimi": "/kimi", "ped": "/pre-earnings-direction"}


def main(n: int = 5) -> int:
    bad = 0
    side_p = HERE.parent / "frontend" / "src" / "components" / "Sidebar.tsx"
    if side_p.exists():
        side = side_p.read_text(encoding="utf-8")
        print("HEADERS")
        for k, name in am.MODELS:
            m = re.search(r"to: '" + re.escape(ROUTES[k]) + r"', label: '([^']+)'", side)
            ok = bool(m) and m.group(1) == name
            bad += not ok
            print(f"  {name:24} sidebar={m.group(1) if m else None!r:24} {'OK' if ok else 'MISMATCH'}")
    sheet = am.scan(15)
    rows = {r["symbol"]: r for r in sheet["rows"]}
    full = [s for s, r in rows.items() if all(c["signal"] != "PENDING" for c in r["cells"].values())]
    pref = [s for s in ("AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "GOOGL", "LLY", "AMD", "XOM") if s in full]
    pick = (pref + [s for s in full if s not in pref])[:n]
    print(f"\nsheet rows={len(rows)} fully-scored={len(full)}  checking: {pick}")
    tabs = {k: am.tab_scan(k, 15)[0] for k, _ in am.MODELS}
    checked = 0
    for s in pick:
        for k, name in am.MODELS:
            t = next((r for r in tabs[k]["rows"] if (r.get("symbol") or "").upper() == s), None)
            c = rows[s]["cells"][k]
            exp = (t.get("signal"), round(t["score"], 1), t.get("coverage")) if t else None
            got = (c["signal"], c["score"], c["coverage"])
            checked += 1
            if exp != got:
                bad += 1
                print(f"  MISMATCH {s} {name}: sheet={got} tab={exp}")
    print(f"\n{checked} cells checked, MISMATCHES: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 5))
