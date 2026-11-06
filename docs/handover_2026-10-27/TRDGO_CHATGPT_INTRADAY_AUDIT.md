# TRDGO.US existing-system discovery audit: ChatGPT Intraday and the Strategy Trades tracker

Prepared 2026-10-27. READ-ONLY: nothing was changed, no trade created or closed, no database or service touched.

## 0. Scope, evidence classes and how to read this

* **Scope.** You wrote "chartgpt intraday" after the audit text, so this audit goes to full depth on **ChatGPT Intraday** (strategy key `chatgpt`) and on the **Strategy Trades paper tracker** that opens and closes its trades. The other five paper-trading boards, the live 5-minute engine and the earnings modules are inventoried (Report 1) but their parameters are not expanded. Say so if you want them done the same way.
* **Which build.** Source cited as `file@36094e8:line` is the **production build** (`36094e89ff78c531bd6c8997208d31f02f1aafee`, what the VPS runs). Source cited as `file@277f30f` is the **release** (developed, not yet running).
* **Evidence classes.** `VERIFIED-CODE` read in source. `VERIFIED-RUNTIME` read from output you pasted from the VPS. `INFERRED` follows from the above but was not observed. `NOT VERIFIABLE` no evidence is available to me. I cannot reach the production database from this laptop (standing rule), so every number about live data comes only from VPS output you pasted.

## 1. Report 1 - Existing strategies

| Strategy | What it is | In code (36094e8) | Evidence it runs |
|---|---|---|---|
| ChatGPT Intraday (`chatgpt`) | 20-parameter, 100-point "institutional" intraday model on 38 names | `chatgpt_intraday_service.py`, registered in `demo_trade_service.MODELS` L28 | Runtime: the tracker loop runs (VERIFIED-RUNTIME, below). Per-strategy trade rows: NOT VERIFIABLE |
| Grok / Meta / Kimi / Gemini / Claude Pro Intraday | Five sibling boards, same board-and-tracker pipeline, own weights | `intraday_grok_service.py`, `meta_intraday_service.py`, `kimi_intraday_service.py`, `gemini_intraday_service.py`, `claude_pro_intraday_service.py`; `MODELS` L24-31; weights in `tab_settings.py` L160-215 | You reported 172 open positions across "six strategies". Per-strategy counts: NOT VERIFIABLE |
| Live 5-minute engine "INTRADAY-v2" | Scores each name every scan; state machine issues tracked live signals (table `intraday_sig_v2`), window 10:00-15:30 ET, EOD close 15:55 | `intraday_model.py`, `intraday_signal_v2.py` (header L1-40) | These are live *signals*, not Strategy Trades paper trades. Running state: NOT VERIFIABLE here (earlier audit: 16 open live signals on 2026-10-26) |
| Trdgo Stock board | Universe board that drives the live engine scan | `ai_trade_service.py`, started by `main.py::_start_ai_trade_board` L746 | VERIFIED-CODE background task; DISABLE_WARMERS not set (VERIFIED-RUNTIME) |
| Strategy Trades tracker | Opens/closes 100-share paper trades from the six boards | `demo_trade_service.py`, thread `demo-trade-tracker` (`main.py` L521-540) | VERIFIED-RUNTIME (see Report 6) |
| Other modules found by route/sidebar name (not audited) | Earnings models (Claude, Grok, Meta, ChatGPT, Kimi, Gemini, Claude Pro, All Models, Pre-Earnings Direction), Tradgo Call, Trade Plan, IV Crush Check, Unusual Activity, Signal History, Options/OI/Volatility/Dark Pool/Insiders, Institutional Intelligence | `frontend/src/components/Sidebar.tsx`, routes in `App.tsx` | Not examined |

Enabled / disabled / running:
* **Enabled by code (production):** all six boards, the 60-second tracker loop, the rolling Trdgo Stock scan, a 300-second watchlist scanner and the other `@_bg` tasks (`main.py` L144-746). `VERIFIED-CODE`.
* **Disabled by configuration:** nothing found. `DISABLE_WARMERS`, the switch that would turn the tracker off (`main.py` L62-74), is **not set** on the VPS (machine, user, `.env`), `VERIFIED-RUNTIME` from the after-close check at 06:59 ET.
* **Unknown:** `INTRADAY_MODEL` on the VPS has duplicate lines in `backend\.env`; the effective value was never shown to me. `NOT VERIFIABLE`. In code it only selects the v2 live-engine routes (`api_routes.py` L1199 in the release; `_v2()`).

## 2. Report 2 - ChatGPT Intraday parameter inventory (all 20, from `chatgpt_intraday_service.py@36094e8` L28-49)

Common mechanics (`claude_last_service.py` L51-80, `tab_settings.py` L60):
* Each parameter gives a raw directional **bias** in -1..+1. `_quant` snaps it to a 5-step scale: bias >= 0.5 -> +1; >= 0.15 -> +0.5; > -0.15 -> 0; > -0.5 -> -0.5; else -1. Missing data stays `None`.
* **Points** = `weight x (0.5 + 0.5 x bias)`. Neutral or **missing = 50% of weight**, strong bull = 100%, strong bear = 0. **Score** = sum of points, rounded to 0.1 (range 0-100; all missing = 50.0).
* **BUY if score >= 55, SELL if score <= 45, else NEUTRAL** (`V3_CUTOFFS = {"buy": 55.0, "sell": 45.0}`, `tab_settings.py` L60; applied at `chatgpt_intraday_service.py` L100 and L201). Symmetric.
* **Two data paths** (`board()` L245-265): before 09:30 ET and overnight/weekends (`market_phase()` "pre" or "closed", `intraday_indicators.py` L88-101) the board scores from **equity signals** (`_equity_board` L209, cached 120 s); in "rth" and "post" (16:00-20:00 ET) it scores from the **live 5-minute engine cache** `im2:<SYM>` (`score_one` L121).
* "Status" below: **Live** = has a data source in the live path; **EqPath** = has one in the pre-market path; **None** = no source in code, always "no data" and scored at 50% of weight (the module docstring says so).

| # | Parameter (code name) | Weight | Live path source -> formula (production) | Pre-market equity path source (`_EQ_SRC` L157-168) | Status |
|---|---|---:|---|---|---|
| 1 | `market_structure` | 10 | none | none | None |
| 2 | `options_flow` | 10 | live `flow`: `clamp(net/norm) x 12`, net = net call premium - net put premium (today), norm = 20-day average (`intraday_model.py` L308-321, `_flow_normalizer` L362). bias = value/12 | `signals["options_flow"]`, else param `options_positioning`. `options_flow()` = `scale(bullish_share%, 25..75) x min(1, prints/20)` (`directional_signals.py` L129) | Live + EqPath |
| 3 | `order_flow` | 9 | `("enrich","order_flow")`: **never available on the board** (enrich is called only on the single-stock detail, `score_one(fetch=True)` L135-150) | `signals["disparity"]`, else param `disparity`. `disparity()` = `clamp(tilt x min(1, stretch/40))` (`directional_signals.py` L868) | EqPath only |
| 4 | `vwap_ema` | 9 | live `vwap_pos`: above VWAP and rising +10, above/flat +5, below/falling -10, below/other -5; slope threshold 0.0005 (`intraday_model.py` L212-225) | `signals["ema_trend"]`, else param `price_action`. `ema_trend()` = share of checks true (close>EMA20, >EMA50, >EMA200, EMA20>50>200) x2 - 1 (`directional_signals.py` L421) | Live + EqPath |
| 5 | `liquidity_sweeps` | 8 | none | none | None |
| 6 | `rvol` | 8 | live `rvol`: sign(move today) x clamp((RVOL-1)/(2-1), 0, 1) x 5 (`RVOL_NONE` 1.0, `RVOL_FULL` 2.0, `intraday_model.py` L29-40, L269-281) | `signals["volume_pcr"]` = `-scale(put/call, 0.85, 1.15)` (`directional_signals.py` L171; constants L30) | Live + EqPath |
| 7 | `rel_strength` | 7 | live `rel_strength`: `clamp((stock %chg - SPY %chg)/1.0) x 5` (`RS_FULL_PCT` 1.0, `intraday_model.py` L282-289) | none | Live only |
| 8 | `absorption` | 6 | none | none | None |
| 9 | `gamma` | 6 | `("either","levels","gamma")`: on the board uses live `levels`: +/-5 when price crossed the largest-OI call/put wall today, +2.5 bouncing within 0.5% of the put wall, 0 within 0.3% of max pain (`_levels_eval`, `intraday_model.py` L480-518). The gamma-flip enrich is detail-only | `signals["key_levels"]` = `scale((spot-put_wall)/(call_wall-put_wall), 0.15, 0.85) x 0.8` (`directional_signals.py` L290) | Live + EqPath |
| 10 | `volume_profile` | 5 | none | none | None |
| 11 | `supply_demand` | 4 | none | none | None |
| 12 | `orb` | 4 | live `orb`: +7 if price > opening-range high and RVOL >= 1, -7 if below the low, else 0 (`intraday_model.py` L238-250) | none | Live only |
| 13 | `unusual` | 3 | live `unusual`: net ask-side premium, calls +, puts -, prints executed today, expiry 0-14 days (`_unusual_eval` L410-431; `UNUSUAL_MAX_DTE` 14) | `signals["unusual_activity"]` = (call vol - put vol)/(call + put) over contracts above baseline (`directional_signals.py` L60) else param `unusual_options` | Live + EqPath |
| 14 | `momentum` | 3 | live `rsi`: RSI(14, 5-min) >75 -> 0; 55-75 -> +5; 50-55 -> +2; <25 -> 0; 25-45 -> -5; 45-50 -> -2 (`intraday_model.py` L251-268) | `signals["rsi"]` (daily RSI(14): bias = scale(RSI, 40, 60), faded beyond 70/30; `directional_signals.py` L453) | Live + EqPath |
| 15 | `breakout_quality` | 2 | none | none | None |
| 16 | `catalyst` | 2 | live `news`: sentiment of today's UW headlines: `clamp((pos-neg)/2) x 5` (`_news_eval` L552-570) | `signals["event_radar"]` (Benzinga + Marketaux analyst actions and tone, `directional_signals.py` L627) | Live + EqPath |
| 17 | `volatility` | 1 | none | none | None |
| 18 | `oi_change` | 1 | `("eq","daily_oi_change")`: `signals["daily_oi_change"]`, magnitude = min(1, abs(change%)/9), sign by whether OI builds on calls or puts (`directional_signals.py` L219) | same | Live + EqPath |
| 19 | `intermarket` | 1 | live `market`: SPY/QQQ each +1 (above VWAP and EMA9>EMA21), -1 (below both), else 0; average x 10 (`_market`, `intraday_model.py` L162-170) | none | Live only |
| 20 | `breadth_mtf` | 1 | `("mkt","breadth")`: `(net call prem - net put prem)/(abs sum)` of the latest UW market-tide point, cached 300 s (`intraday_enrich.py` L23-45) | same | Live + EqPath |

Totals (computed from the table):
* Weight with **no source at all**: 10+8+6+5+4+2+1 = **36 of 100**.
* Weight reachable on the **live board path**: 55 (rows 2, 4, 6, 7, 9, 12, 13, 14, 16, 18, 19, 20). Row 3 (9) is detail-only.
* Weight reachable on the **pre-market equity path**: 52 (rows 2, 3, 4, 6, 9, 13, 14, 16, 18, 20). So at best **48 of 100 weight is permanently neutral** before the open.
* Current values: weights above are the configured values. **Actual readings at entry are not recorded** (Report 5).
* Data sources: Unusual Whales (bars, quote, options volume, flow, news, market tide), option chain, daily bars (UW candles), Benzinga + Marketaux (event radar). Timeframes: live path 5-minute bars, options tape = today's session; equity path daily bars and cached snapshots (`EARN_CACHE_TTL = 48 h`, `tab_settings.py` L348).

Shared helpers used for levels (`intraday_grok_service.py@36094e8`): entry price = Unusual Whales quote price, cached 120 s (`_price_atr` L114-129); ATR = 14-period **daily** ATR, cached 6 h; `_levels` L132-146: stop = price -/+ 0.5 x ATR, target = price +/- 1.0 x ATR (2:1). The live path takes spot and ATR from the engine result (`chatgpt_intraday_service.py` L105-111).

## 3. Report 3 - BUY/SELL decision logic (production)

1. Score = sum of points (Report 2). `BUY` if >= 55, `SELL` if <= 45. Nothing else is consulted in the ChatGPT scoring code: **no coverage minimum, no agreement test, no market-regime gate, no earnings or liquidity exclusion** (`MIN_COVERAGE_PCT = 70` and `V3_CONFIDENCE` exist in `tab_settings.py` L337/L62 but `chatgpt_intraday_service.py` never reads them). The live engine's own gates G1-G7 (`intraday_model.py` L672-713) are **not applied**: ChatGPT reads only the engine's parameter values (`_score` L52-70), not its `call` or `gates`.
2. Arithmetic consequence (VERIFIED-CODE): a missing parameter counts as 50%, so all-missing = 50.0. On the pre-market path 48 weight is always neutral (24.0 points) and the other 52 weight is neutral at 26.0. Reaching BUY needs only **+5.0 points** above neutral, e.g. `options_flow` alone at +1 (10 points instead of 5) gives exactly 55.0 = BUY; the same at -1 gives 45.0 = SELL. Maximum possible swing on that path is +/-26 points.
3. Direction: BUY -> LONG, SELL -> SHORT (`demo_trade_service.py` L246-247).

## 4. Report 4 - Automatic trading workflow (production)

1. **Trigger.** A daemon thread `demo-trade-tracker` loops every 60 s: `if dts.should_tick(): dts.tick(force=True)` (`main.py` L521-540). It is registered with `@_bg` and does not run if `DISABLE_WARMERS` is set (`main.py` L62-74).
2. **When it ticks.** `should_tick` (L79-85): while `_can_open` (weekdays 04:00-20:00 ET, `_EXT_OPEN/_EXT_CLOSE` L55-60), or after 16:00 ET while any trade is still OPEN.
3. **Boards.** Each tick calls `board()` of every strategy in `MODELS` (L24-31); ChatGPT's returns BUY/SELL/NEUTRAL rows for the 38 names of `ai_trade_service.UNIVERSE` (L48-57: 36 stocks + SPY + QQQ). Rows are flattened by `_row_map` (L121-131): signal, score, entry, now, sl, tp.
4. **Open condition** (`tick`, entry block ~L236-255): `signal in (BUY, SELL)` **and** `entry` is a non-zero price **and** no row already exists today for this `(strategy, symbol)`. That is all: no freshness test, no 15-minute confirmation, no data-coverage test, no equity-only filter (SPY and QQQ qualify), no cap on concurrent positions.
5. **Open action.** One `DemoTrade` row: `shares=100` fixed (L248), `entry_price` = board entry (quote price), `stop`/`target` from the board (ATR-based) or the default 1% / 2% (`_with_levels` L183-190) when the board has none, `score_at_entry`, `status="OPEN"`.
6. **Manage.** Every tick, for OPEN rows with `session_date = today` (L214): last price = board `now` or a quote; P&L = (last-entry) x shares for LONG, reverse for SHORT.
7. **Close rules** (`_exit_reason` L141-153, `_exit_timed` L88-96), first match wins: `STOP` (last <= stop for LONG / >= stop for SHORT); `TARGET`; `SCORE_EXIT` when the board score moves 15 points against the entry score (`SCORE_DROP_EXIT = 15.0`, L20); `TIMED_EXIT` at/after the 16:00 ET close for trades entered before 16:00 (or carried from a previous day, which the today-only query never loads).
8. **Fill price on exit** (L222-227): TARGET closes at the target, STOP at the stop, others at the last price. No slippage or gap model in production.
9. **Position size / risk.** Fixed 100 shares; stop distance 0.5 x daily ATR(14) (so dollar risk = 50 x ATR); no daily loss limit, per-symbol or portfolio limit found in code. `VERIFIED-CODE`.

## 5. Report 5 - Current trades investigation

* **What can be said from the VPS output you pasted** (all `VERIFIED-RUNTIME`, 2026-10-27 06:59 ET): the table holds OPEN 172, SCORE_EXIT 122, STOP 246, TARGET 64, TIMED_EXIT 200 (all history); **last entry 04:00:25 ET, last exit 04:03:27 ET**; no entries in the last 15 minutes; all 172 open rows opened before 16:00; none carried; none older than 30 hours; 95 of the 172 rows refreshed in the previous 5 minutes. You also reported that the 172 belong to six strategies, all opened at 04:00 ET.
* **Why many trades opened at about the same time** (`INFERRED` from code + runtime): the tracker only starts opening at 04:00 ET (`_can_open`). The six boards, in the "pre" phase, score from **cached equity snapshots** (`_equity_board`), which already hold BUY/SELL results before 04:00, so the first tick after 04:00 opens every qualifying `(strategy, symbol)` in a single pass (maximum 6 x 38 = 228). The first tick lands on the 60-second loop, consistent with 04:00:25.
* **Real strategy conditions, schedule, or another mechanism?** Both: the **entry condition** is a board score at or past 55/45 (a strategy condition); the **timing** is the clock gate at 04:00 plus the 60-second loop (a scheduled mechanism). No manual or external trigger exists in the code path (`VERIFIED-CODE`: only `tick` inserts into `demo_trades`; no broker or order code exists in the application, `git grep` of `36094e8` finds order-placing terms only in `tests/test_no_trading.py`).
* **Per-trade facts requested** (symbol, direction, entry time/price, quantity, stop/target, score at entry, status): the table `demo_trades` stores exactly these (`models_demo_trades.py` L30-50) but **I have not seen the rows**. `NOT VERIFIABLE` until the read-only query below is run.
* **Parameter readings at entry: NOT RECORDED.** `demo_trades` has no column for them, and `signal_audit_log` is the live engine's log, not the paper trades'. Only `score_at_entry` exists, so which parameter drove an entry cannot be reconstructed from stored data. `VERIFIED-CODE`.
* **Risk controls actually applied to each trade:** the ATR stop/target, the 15-point score-drop exit and the 16:00 timed exit above. Nothing else.
* **Read-only query to complete this report** (not run): `docs/handover_2026-10-27/phaseB/phaseB_chatgpt_trades_evidence.ps1` (parse and syntax checked only). It lists every ChatGPT trade today with entry second, price, stop/target distance, score at entry and status, plus the entry-time clustering for all strategies.

## 6. Report 6 - System verification (source vs configuration vs runtime)

| Function | A. Source (production 36094e8) | B. Configuration | C. Runtime evidence | Verdict |
|---|---|---|---|---|
| Paper tracker loop | present (`main.py` L521-540) | `DISABLE_WARMERS` not set (VPS) | entries 04:00:25 ET, exits through 04:03:27, 95 rows refreshed in 5 min, history of TIMED_EXIT closes | **Active** |
| ChatGPT board, pre-market path | present | n/a | consistent with the 04:00 batch (inferred) | Active (strategy attribution NOT VERIFIABLE) |
| ChatGPT board, live path (09:30-20:00 ET) | present | needs the `im2:` cache from the live scan | not observed | **Unverified** |
| 7 of 20 ChatGPT parameters | declared with no source | n/a | n/a | **Unused** (always neutral) |
| 3 of 20 (`order_flow`, partly `gamma`) | detail-view enrichment only | n/a | n/a | Unused on the board |
| 15-minute confirmation, data-freshness gate, entry lock | **not in production** (files absent at 36094e8: `intraday_safety.py`, `intraday_15m.py`, `intraday_feed.py`, `entry_lock.py`, `paper_fills.py`, `market_calendar.py`) | n/a | n/a | Developed in the release, **not running** |
| `INTRADAY_MODEL` | present | value unknown (duplicate `.env` lines) | n/a | **Unverified** |
| Live-engine gates G1-G7 for ChatGPT | present in `intraday_model.py` | n/a | n/a | **Not used** by ChatGPT |
| Carried-position handling | tracker loads only today's OPEN rows | n/a | 0 carried at 06:59 ET | Gap in code, not exercised today |

## 7. Report 7 - Findings (discrepancies, missing evidence, issues; no changes proposed)

1. **Source vs production.** The release (`277f30f`) adds a data-freshness gate, a mandatory 15-minute confirmation and an entry lock to exactly this pipeline; **none of it runs in production**. The 172 positions were opened by the older logic.
2. **Entries do not check data age.** `_eq_cached` serves whatever `earn:equity:<SYM>` exists (TTL 48 h); the pre-market trades can rest on snapshots up to ~48 h old. The actual age at entry is `NOT VERIFIABLE`.
3. **Thin conviction needed.** Net +5.0 / -5.0 points around 50 triggers BUY/SELL; one strongly bullish `options_flow` reading is enough (Report 3).
4. **Large neutral base.** 36 of 100 weight has no data source at all; before the open 48 of 100 is neutral by construction, and coverage is not enforced.
5. **Live gates bypassed.** ChatGPT ignores the live engine's earnings, liquidity, market-alignment and confluence gates.
6. **No parameter evidence stored.** Only `score_at_entry`; the reason for each entry cannot be audited later.
7. **Exit modelling is idealised.** STOP/TARGET fill exactly at the level; pre-market quotes and gaps are not modelled.
8. **Risk controls are minimal.** Fixed 100 shares, no portfolio, daily-loss or concurrency limits; ETFs (SPY, QQQ) are traded alongside stocks.
9. **Open/close asymmetry.** Trades may open until 20:00 ET but only trades entered before 16:00 are closed by the timed exit; carried trades are never loaded by the manager (`tick` L214).
10. **Unknown configuration.** The effective `INTRADAY_MODEL` and the per-strategy trade counts were not shown.
11. **Release-only note (not production).** On the laptop dev build, `calls`/`snapshots` endpoints returned 500 because of a dev-only table-isolation bug (see the earlier local-test report); production is believed unaffected.

STOP. No further action taken.
