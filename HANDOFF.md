# TRDGO-US — handoff

Written 2026-11-23 for whoever picks this up next. Branch **`release/kit-n`**, worktree
`C:\Users\initi\Downloads\US-STOCKS\.worktrees\stage2`.

Production is a Windows VPS: app at `C:\apps\us-stock-reader`, service `USStockReader`, site
<https://tradgo.us>. **VPS clock = ET + 6. Dubai = ET + 9 = VPS + 3.**

---

## 1. Project layout

```
backend/            FastAPI app. Entry: main.py; routes in api_routes.py.
  earnings_monitor/ Smart Money Move (PROTECTED - see rules)
  tests/            pytest
frontend/           React + Vite + TypeScript
  src/              new UI: New*.tsx pages, new-*.css, components/
  tests/            node:test + react-dom/server; `npm run test:ui`
  figma-demo/       DEV ONLY, gitignored, never ships. Fixture backend + harnesses.
deploy/             kit installers (make_*.py -> *_run.ps1) and the deploy package
docs/               audits, kit notes, setup guides
```

### Run it locally

```powershell
# backend (uses the DEV database - never point it at production)
cd backend ; python -m uvicorn main:app --reload --port 8000

# frontend
cd frontend ; npm install ; npm run dev
```

**Tests — these are the gate for every kit:**

```powershell
cd frontend ; npm run test:ui          # 67 pass
cd frontend ; npx tsc --noEmit -p tsconfig.app.json
cd frontend ; npm run build
cd backend  ; python -m pytest tests/ -q -p no:warnings -k "symbol or directional or chart or market or analysis or screener or movers or universe"
```

Three traps that have each cost a day:

- **`tsconfig.app.json` is what `npm run build` uses.** `tsconfig.json` is looser and misses real
  errors. Always typecheck with the app config.
- **`python -m pytest`, not `python -I -m pytest`.** `-I` strips the working directory from the
  import path and every project import fails.
- **The whole backend suite has 60–70 pre-existing failures** across unrelated modules, measured
  identical on a pristine baseline. Gate on the targeted selection above, not the full run.

### The dev fixture (how UI work gets verified without production data)

`frontend/figma-demo/_fixture_api.py` serves the real JSON *shapes* with obviously-fake values on
`127.0.0.1:8000`. It is gitignored and ships in no kit.

**It has twice made a measurement meaningless**, so check it before trusting a local result: it
reported `market: null`, so the market-aware polling back-off correctly never engaged and a
before/after came out identical; and it has no Monday-BMO summary data, which is why a card-clipping
bug reached production. **If a local check shows "no change", suspect the fixture first.**

`CLOSED=1` makes it return the literal `0` bid/ask the provider sends out of hours.

---

## 2. Kits

Each kit = a commit containing a built package + a pinned PowerShell runner. The runner downloads
the GitHub archive of the pinned commit, verifies the manifest anchor, backs up, copies, restarts.

| kit | commit (package) | manifest anchor | runner SHA-256 | state |
|---|---|---|---|---|
| N | `aefae021…` | `62059b46…` | — | live |
| O | `38812430…` | `64636567…` | — | live |
| P | `3b584832…` | `0db45073…` | `41EDCD54…` | **live** 2026-11-22 |
| Q | `ee7ee0a1…` | `2505c3ca…` | `DFB0F88A…` | **live** 2026-11-23 04:39 VPS |
| R | `d4b7bab6…` | `8d11a731…` | `57E35FD1…` | **live** 2026-11-23 09:22 VPS |
| S | `5b8ac6a3ab28fe75011d58e32bc09fcd01e34823` | `859ff0472057ee21e69112bfb03a131e10aa5df8d68edc037128cdc795872bb6` | `3C3264DFCEED5E823CF04E6F068F001BED22B8CB10AD9ED0CCFFADB307C1A339` | **live** 2026-11-23 13:42 VPS |
| T | `c1601f538c9953ee3f3bb7f4154763c380f39fb8` | `6362f0304ccc6e8b4063a40192e693cb461cb9ab20b5e889bf5ba4790b69da09` | `88CE2CCF0F323A2C9EBA79C54F60A6A93BF286C339F4879269707D575956E912` | **live** 2026-11-25 10:28 VPS (installer `8aaaa5a7`, backup `_backup_t1_20261125_102820`; frontend only: Strategy Trades live event strip) |
| U | `5a9a16c8e7a429e96801793b3de32d31c491ff2a` | `df9089b1c5717ad8c20c69f05333129984e2e485ba4ce9f96a7e42146514d927` | `3F944A97473B55527F7F96FFE60D5860C701D6114D39D0E4F6F2B9EE67681217` | **live** 2026-11-26 (installer `ac643cee`; owner installed, output not pasted; verified on the live site: hero cards gone, two menu entries hidden, My Broker redesign, IV Crush refill active - most refilled rows still come back empty, see docs/KIT_U_PROGRESS.md) |

Kit S installer commit: **`ef9b90a6`**. Runner URL:
`https://raw.githubusercontent.com/syed583/trdgo.us/ef9b90a6/deploy/s1_run.ps1`

Rollback points on the VPS:
`_backup_p1_20261122_200122`, `_backup_q1_20261123_043922`, `_backup_r1_20261123_092245`,
`_backup_s1_20261123_134229`
(under `C:\apps\us-stock-reader\`).

### What kit S contains (pending install)

1. **The AttributeError hotfix.** `live_market_service._bars_fallback_note` read
   `provider.last_status`, which does not exist (the module keeps `_last_status`). It fired on
   **any empty daily chart** — thin ticker, outage, exhausted budget — not just unknown symbols.
   Fixed with a public `unusualwhales_service.last_status()` accessor, read defensively.
   The same wrong name in `api_routes.py:3772` sat behind a `hasattr` guard. **Correction to an
   earlier claim of mine:** that endpoint was NOT always answering `NO_DATA`. The broken call is
   only in the EXCEPTION fallback - the normal path reads `uw.quote(sym).get("status")` and has
   always worked. It degraded to `NO_DATA` only when a quote call threw, losing the real reason.
   The accessor fixes that path; it is visible only during a provider failure.
2. **Unknown symbols no longer run the 12 agents.** The SEC registry (free, cached 24h) is checked
   first; the feed is consulted only for symbols the registry does not list. A real company pays
   nothing extra; a typo costs 1 request instead of 12. Fails open at every step.
3. **Market-aware polling** — intervals ×6 when the session is a confirmed `CLOSED`.
   `PRE_MARKET`/`AFTER_HOURS`/unknown stay at full rate. Measured ~19 → ~3 calls/min idle on live.
4. **Telegram DB-down watchdog** (`deploy/db_watchdog.ps1`) — **NOT installed by the kit**, see §4.
5. Card labels no longer break mid-word.

Verified live after install: `ZZZQQX` -> 0 agents, 280 ms, "No US stock listed as ZZZQQX.";
`P` -> 12 stages, no AttributeError; NVDA unchanged; 8/8 Smart Money labels intact, 0 values
clipped.

### Install procedure

```powershell
# 1. download + hash check (NEVER skip the hash)
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/syed583/trdgo.us/<INSTALLER_COMMIT>/deploy/<k>_run.ps1" -OutFile "C:\SmartMoveRun\<k>_run.ps1"
(Get-FileHash "C:\SmartMoveRun\<k>_run.ps1" -Algorithm SHA256).Hash

# 2. lock check - Update is REFUSED while a Smart Move lock exists
Get-ChildItem "C:\apps\us-stock-reader\sm_collector_state","C:\apps\us-stock-reader\sm_evidence_store" -Filter "*.lock" -ErrorAction SilentlyContinue

# 3. PreCheck (read-only) -> 4. Update -> 5. PostCheck
powershell -ExecutionPolicy Bypass -File C:\SmartMoveRun\<k>_run.ps1 -Mode PreCheck
powershell -ExecutionPolicy Bypass -File C:\SmartMoveRun\<k>_run.ps1 -Mode Update -ConfirmWord YES-INSTALL-<K>1 -ApproveCommit <COMMIT> -ApproveManifest <ANCHOR>
powershell -ExecutionPolicy Bypass -File C:\SmartMoveRun\<k>_run.ps1 -Mode PostCheck

# rollback
powershell -ExecutionPolicy Bypass -File C:\SmartMoveRun\<k>_run.ps1 -Mode Rollback -ConfirmWord YES-ROLLBACK-<K>1
```

Flags are **`-ConfirmWord`, `-ApproveCommit`, `-ApproveManifest`** — all three, and `-ApproveCommit`
is not optional. Read the runner's own header (lines 7–10) rather than copying a previous letter's
invocation from memory.

**For kit S specifically, PreCheck must read `backend files: 4 changed`.** If it says 0, the hotfix
is not in the package and installing achieves nothing.

### WHEN you may install — the lock timetable

The Smart Move lock is held by:

- **the collector, every hour :05 → :35**
- **the LIVE pass, at :45 on trigger hours**

LIVE triggers: **05:45 / 11:45 / 14:35 / 23:45 VPS**. Attestation renewal: **23:00 VPS** daily.

The reliably clear window is **:40 past the hour on a non-trigger hour**. Do not delete a lock —
a collector mid-write will corrupt evidence files. If a lock looks stale, check
`schtasks /query /tn "TRDGO-SmartMove-LIVE-6h" /fo LIST /v`; **`Last Result: 267009` means
"currently running"**, not an error.

### Writing the next kit's installer

`make_<next>.py` is derived from the previous letter's. Two traps, both of which have bitten:

1. **Do the targeted edits BEFORE the blanket letter bump.** Reversed, the bump rewrites the anchors
   first and every targeted edit silently matches nothing — which left one generator pointing at
   the wrong letter entirely.
2. **`.ps1` contains `s1`.** From kit S onward the blanket bump must mask the extension or every
   `.ps1` becomes `.pt1`. `make_s1.py` shows the masking pattern.

Always assert on every replacement anchor. Silent no-ops are the recurring failure mode here.

---

## 3. Rules — not negotiable

- **Real data only.** Never invent a number to fill a panel. If a value is missing, say so.
  A bid of `0` is not a bid; a flat `$0.00` is not a profit.
- **Admin-only stays admin-only.** Model names, weights, parameters, engine versions and blocker
  codes are admin-only. Users see Agent 1–4 and plain wording.
- **Never touch without explicit approval:** `A.27` intraday rules, order/trade logic, and
  `backend/earnings_monitor/smart_move*` + the VPS `C:\smlive\` scripts.
- **Never push without being asked.** Never install without a slot.
- **Never run tests, probes or a local backend against the production database.**
- **Secrets never go in the repo or in chat.** The owner pastes them into files on the server.
- **Verify on the live site after every install.** Two kits in a row shipped defects the local
  fixture could not reproduce; both were caught within minutes by looking at the real page.

---

## 4. Open tasks

### Needs the owner before anything can start

- **Telegram DB-down watchdog — setup, ~10 min.** The script ships in `deploy/db_watchdog.ps1` but
  **no kit installs it**. Steps in `docs/DB_WATCHDOG_SETUP.md`: BotFather → token; message the bot
  then `getUpdates` → chat id; create `C:\SmartMoveRun\watchdog_secrets.ps1` with
  `$TelegramBotToken` and `$TelegramChatId`; run `-TestMessage` until a message actually arrives;
  **only then** register the per-minute scheduled task. Written after a 40-minute outage on
  2026-11-23 that users noticed before we did.
- **MT5 P&L gap** — blocked on `/api/mt5/*`, which needs an admin login. The software book is
  $10,000-notional (6–733 shares); MT5's fixed 10 shares is **not** a 0.1× scale, so sizing is not
  the explanation. A fixed-10 mirror would have made **+$3,203** against the **+$741** a true 0.1×
  implies.
- **Smart Money totals cards** — owner wants "total closed" and "total profit".
  `/api/strategy-trades` is **date-scoped only** and no cumulative endpoint exists (six candidates
  probed, none present). Needs a new read-only aggregate. Owner to choose: all-time / last 30 days
  / keep date-scoped with a clearer empty state.
- **Takion auto-trading** — audit done, **build not started**. The API is a **C++ DLL loaded inside
  the Takion desktop app**, not a network API, so there is no local endpoint for a connector to
  call. Blocked on three answers from Takion: (1) may a third-party DLL trade a client account —
  **get this in writing first, everything else is wasted without it**; (2) is there a network API
  today; (3) current SDK package + demo account. Also needs an EV code-signing certificate and a
  decision on the EOD rule, since a 15:45 ET flat-by cannot be guaranteed on a user's PC.
  Full report is in this conversation; architecture B (thin DLL bridge + C# connector) recommended.
  **Server half can be built without Takion** and is reusable: per-user token, signals endpoint,
  `POST /fills` with a server-issued idempotency key, symbol mapping, share quantity, reconciliation.

### Next small kit — both agreed with the owner 2026-11-23

1. **`covered: false` must skip the 12 agents.** Kit S made a genuinely unknown ticker cost zero
   agent calls, but a symbol the SEC registry lists while the market feed cannot cover it still
   runs the full pipeline. `"P"` is the live example:

   ```
   /api/symbols/validate/P
   {"symbol":"P","name":"Everpure, Inc.","cik":"0001474432","valid":true,"covered":false,
    "detail":"P is a registered issuer, but the market feed cannot cover it"}
   ```

   So `known_to_sec("P")` is correctly true, the pre-check steps aside, and the user waits several
   seconds for a meaningless result. **Wanted:** treat `covered: false` as not analysable and show
   **"No market data for this symbol"** - a different message from "No US stock listed as X",
   because the two are different facts and the page should say which one applies. Never start the
   agents. **Add a test** alongside the existing ones in
   `backend/tests/test_analysis_unknown_symbol.py`.

   It is a one-line extension of the same pre-check in `directional_score_service.get_directional_score`
   - read `covered` from `symbol_service.validate()`. Keep it failing open: a missing `covered`
   field, or any error reading it, must let the analysis proceed.

2. **DB watchdog is written but NOT installed.** `deploy/db_watchdog.ps1` ships in the repo and no
   kit installs it (`build_package.py` ships only `backend/` + `frontend_dist`). The owner will set
   the Telegram bot token and chat id himself later, following `docs/DB_WATCHDOG_SETUP.md`:
   BotFather -> token; message the bot then `getUpdates` -> chat id; create
   `C:\SmartMoveRun\watchdog_secrets.ps1`; run `-TestMessage` until a message actually arrives;
   **only then** register the per-minute scheduled task. Do not register it before the test message
   works, or the first sign of trouble will be silence.

### Code work, unblocked

- **Remaining click-test pages.** All 23 menu pages were layout-audited; findings 24 → 5 real.
  Still open: ~3 small text-row overflows on Analysis Intraday, 1 on Strategy Trades (28px), and
  TradingView's own widget asking for 10px more than its box. **Not yet done: a functional pass** —
  verifying each control does the *right* thing on live data, and the **admin pass**, which needs
  an admin login.
- **Vendor names on the 4 hidden pages** — the boards hidden from the menu in A.26 are still
  reachable by URL and have not been through the B.1 model-privacy pass.
- **Data defects found 2026-11-23, none fixed** (these are data, not layout):
  - **Market sentiment reads 100/100 "Extreme greed" from ONE component over 6 names**, while
    describing itself as a composite of breadth + volatility + options. VIX is `NO_DATA` so the
    volatility leg is gone and what remains pegs the gauge at maximum.
  - **NVDA shown at two prices with opposite signs on one screen** — `/api/movers` 127.53/−12.64%
    vs `/api/quote` 130.01/+3.57% (previous close 125.53, so 127.53 would be +1.59%).
  - **Dow Jones and VIX return `NO_DATA`**; Russell 2000 returns a price with a null change and
    never renders.
  - **The "Try the demo" button does nothing** — sets `?demo=1` and stops.
  - **Top Movers is down to 4 rows** — a consequence of kit P's ETF filter working as designed
    (14 dropped as not common stock, 2 below the price floor).
  - **`/earnings/:symbol` has a second crash** of the class fixed in kit P: `StockHeader` reading
    `.change` of undefined when the payload lacks the field.

### Watch

- **Tonight's 23:45 VPS pass** is the proof that the attestation fix holds. Renewal moved
  00:10 → 23:00 on 2026-11-23 because the 23:45 pass needed validity until 00:15 and the
  attestation died at 00:10 — **it missed by five minutes, every night**. Rollback:
  `schtasks /change /tn "TRDGO-SmartMove-Attestation-Renew" /st 00:10`
  (backup XML at `C:\backups\attest_renew_20261123.xml`).
- **Monday BMO runs 22:35 VPS**; if a pass ever overruns to 23:00 it could collide with the renewal.

---

## 5. Secrets — copy these yourself, they are NOT in the zip or the repo

| file | holds |
|---|---|
| `backend/.env` | `DATABASE_URL`, `UNUSUAL_WHALES_API_KEY`, `ACCESS_PASSWORD`, session secret, MT5, WhatsApp/OTP, `ANTHROPIC_API_KEY`, `FINNHUB_API_KEY` |
| `C:\SmartMoveRun\watchdog_secrets.ps1` | Telegram bot token + chat id (create it; see §4) |
| `C:\smlive\` (VPS) | Smart Move runtime + attestation. **Protected — do not edit.** |

`backend/.env.example` lists the variable names with blank values. The database is Supabase
(Frankfurt, `eu-central-1`, project `ddetojbocaxhcfxtcopc`, **pooler port 6543**). It caps at
**15 connections** — a local backend pointed at production will break live login.

**On 2026-11-23 the project was suspended over an unpaid invoice**: every login returned HTTP 500
and every full-access account silently showed as "View only", because the role lookup could not
read the user row. If both symptoms appear together, check Supabase billing before reading any code.
