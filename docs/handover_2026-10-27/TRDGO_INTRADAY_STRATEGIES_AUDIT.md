# TRDGO.US intraday strategies: complete read-only audit

Prepared 2026-10-27. READ-ONLY: no code, parameter, record, trade or service was changed. No recommendations are made.

Scope: ChatGPT, Grok, Meta, Kimi, Gemini and Claude Pro Intraday, plus the Strategy Trades tracker only where these use it. Earnings, swing and unrelated modules are excluded (Gemini Intraday contains company/earnings-type *parameters*; those are in scope because they are part of its intraday score).

## Evidence key and sources
* `VERIFIED-CODE` read in source. `VERIFIED-RUNTIME` read from output you pasted from the VPS. `INFERRED` follows from those but was not observed. `NOT VERIFIABLE` no evidence is available to me.
* **`@36094e8:line`** = production build (what the VPS runs). **`@277f30f`** = newer release (developed, not running). I cannot reach the production database or logs from this laptop, so all runtime evidence is what you pasted (after-close check 2026-10-27 06:59 ET, B5 preflight, paper investigation).
* Files: `chatgpt_intraday_service.py`, `intraday_grok_service.py`, `meta_intraday_service.py`, `kimi_intraday_service.py`, `gemini_intraday_service.py`, `claude_pro_intraday_service.py`, `demo_trade_service.py`, `claude_last_service.py`, `tab_settings.py`, `directional_signals.py`, `earnings_equity_service.py`, `directional_score_service.py`, `intraday_model.py`, `intraday_indicators.py`, `intraday_enrich.py`, `swr.py`, `main.py`, `models_demo_trades.py` (all `@36094e8`).

---
## Report A: strategies and status

| Strategy (key) | Engine | Universe | Registered | Status |
|---|---|---|---|---|
| ChatGPT (`chatgpt`) | live 5-minute engine params while `market_phase` is `rth` or `post`; equity signals while `pre`/`closed` | 38 names `ai_trade_service.UNIVERSE` L48-57 (36 stocks + SPY + QQQ) | `demo_trade_service.MODELS` L28 | Code active. Per-strategy trades NOT VERIFIABLE |
| Claude Pro (`claude_pro`) | same two-path design, own 8 blocks | same 38 | `MODELS` | same |
| Grok (`grok`) | equity signals only (`earn:equity:<SYM>`) | same 38 | `MODELS` | same |
| Kimi (`kimi`) | identical code to Grok, different weights | same 38 | `MODELS` | same |
| Meta (`meta`) | equity signals only, 8 signals | same 38 | `MODELS` | same |
| Gemini (`gemini`) | equity signals only, 11 blocks incl. company/earnings signals | same 38 | `MODELS` | same |
| Strategy Trades tracker | thread `demo-trade-tracker`, 60 s loop, `main.py` L521-540 | the six boards | `@_bg` (off only if `DISABLE_WARMERS` is set, `main.py` L62-74) | **Active** (VERIFIED-RUNTIME: `DISABLE_WARMERS` unset on the VPS; entries 04:00:25 ET; exits to 04:03:27 ET; 95 of 172 open rows refreshed in 5 min) |

Nothing is disabled by configuration that I could find (`VERIFIED-CODE`, plus the `DISABLE_WARMERS` result above). The effective `INTRADAY_MODEL` on the VPS is `NOT VERIFIABLE` (duplicate `.env` lines, value never shown); in code it only selects v2 live-engine routes (`api_routes.py` `_v2()` L1199 in the release).

---
## Report B: complete parameter inventory (production)

### B0. Machinery common to all six (VERIFIED-CODE)
* Bias to points (`claude_last_service.py` L51-80): `_clamp` to -1..+1; `_quant`: >= 0.5 -> +1, >= 0.15 -> +0.5, > -0.15 -> 0, > -0.5 -> -0.5, else -1; `None` stays `None`. `_pts(weight, bias) = weight x (0.5 + 0.5 x bias)`; **bias `None` (missing) = 0.5 x weight; bias 0 (neutral) = 0.5 x weight**. `_avg` ignores `None` members (L179).
* **Score = sum of points, rounded to 0.1.** All inputs missing => exactly 50.0.
* **BUY if score >= 55; SELL if score <= 45; else NEUTRAL** (`V3_CUTOFFS` `tab_settings.py` L60). Same cutoffs for all six. Missing-data fallback: neutral, scored at 50% of weight; **no minimum coverage is enforced** (see B7).
* Level formula for every board (`intraday_grok_service._levels` L132-146; ChatGPT/Claude Pro live path: `chatgpt_intraday_service.py` L105-111, `claude_pro_intraday_service.py` L67-75): entry = price; stop = price -/+ 0.5 x ATR; target = price +/- 1.0 x ATR (BUY: stop below, target above; SELL reversed). ATR = daily ATR(14), Wilder (`intraday_indicators.atr14_daily` L257). Equity path price = Unusual Whales quote, cached 120 s; ATR cached 6 h (`_price_atr` L114-129).

### B1. The shared equity signals (every board reads these from `eq["signals"]`, built in `directional_signals.py`; bias -1..+1)
| Signal | Formula (`directional_signals.py@36094e8`) | Source | Missing |
|---|---|---|---|
| `options_flow` L129 | `_scale(bullish_share%, 25, 75) x min(1, prints/20)` (needs >= 20 prints for full strength) | Unusual Whales per-print tape | `None` |
| `unusual_activity` L60 | `(call_vol - put_vol)/(call_vol + put_vol)` over contracts above their baseline | Unusual Whales | `None` |
| `volume_pcr` L171 | `-_scale(put/call, 0.85, 1.15)` | option chain | `None` |
| `oi_positioning` L197 | `_scale(call_oi/total_oi x 100, 40, 60)` | option chain | `None` |
| `daily_oi_change` L219 | `m = min(1, abs(change%)/9)`; on CALLS: `+m` if OI rose else `-0.5m`; on PUTS: `-m` if rose else `+0.5m` | Unusual Whales OI history | `None` |
| `flow_by_expiry` L258 | `_scale(calls/(calls+puts) x 100, 40, 60)` across expiries | option chain | `None` |
| `key_levels` L290 | `_scale((spot-put_wall)/(call_wall-put_wall), 0.15, 0.85) x 0.8` | option chain | `None` |
| `disparity` L868 | `clamp(tilt x min(1, stretch/40))` over 13 option-market readings | Unusual Whales + chain | `None` |
| `ema_trend` L421 | share of {close>EMA20, >EMA50, >EMA200, EMA20>EMA50>EMA200} true, x2 - 1 | daily bars | `None` |
| `rsi` L453 | RSI(14) daily: >= 70 -> `max(0, 1-(rsi-70)/20)`; <= 30 -> `min(0, -1+(30-rsi)/20)`; else `_scale(rsi, 40, 60)` | daily bars | `None` |
| `price_action` L484 | `clamp(score/max_score)` of swing structure, breakout, volume confirmation, relative strength | daily bars | `None` |
| `event_radar` L627 | analyst actions + news tone bias from the events payload | Benzinga + Marketaux | `None` |
| `implied_volatility` L330 | `_scale(IV rank, 0, 100)` | provider IV rank | `None` |
| `expected_move` L359 | (not scored in any intraday weight table; see Grok/Kimi `volatility_em` = 0) | | |
| `insider_activity` L512 | Form 4 buys-sells share (+0.35 on a cluster; net selling x0.5), 13D/G activists/2, blended 5:1 | SEC EDGAR | `None` |
| `earnings_results` L589 | `_scale(beat_rate, 0.35, 0.85) x 0.7 + clamp(avg_surprise/30, +/-0.3)` | Benzinga / Finviz | `None` |
| `merger_activity` L708 | recency-weighted mean of fixed pulls per filing type (takeover bid +0.9, material agreement ended -0.3, ...) | SEC EDGAR | `None` (0.0 when no deal filings) |
| `funding_activity` L944 | same method with negative pulls (share offering -0.5, bankruptcy -1.0, ...) | SEC EDGAR | `None` (0.0 when none) |
| `dividend_trend` L831 | rising +0.5, flat 0, cut -0.9; no dividend 0.0 | Nasdaq | `None` |
| param `short_insider` (`earnings_equity_service.py` L351) | mean of insider bias and squeeze tilt `0.5 x mean(min(1, days_to_cover/10), min(1, short%float/20%))` | UW screener + EDGAR | `None` |
| params `options_positioning`, `unusual_options`, `price_action`, `disparity` (`earnings_equity_service.get_analysis` L398-460) | used as fallbacks: positioning = mean(options_flow, oi_positioning, institutional); unusual_options = mean(unusual_activity, flow_by_expiry); disparity = mean(disparity, volume_pcr) | | |

Freshness of the equity signals (production): boards read `earn:equity:<SYM>` with `swr.peek`, which returns the held value **whatever its age** (`swr.py` L177-190); build TTL `EARN_CACHE_TTL = 48 h` (`tab_settings.py` L348); underlying `directional:<SYM>` TTL 120 s open / 900 s closed (`directional_score_service.py` L46-47). Actual age at entry is `NOT VERIFIABLE`.

### B2. Grok Intraday (weights `tab_settings.py` L160-171; `score_one` `intraday_grok_service.py` L43-100)
| Parameter | Wt | Bias formula | BUY / SELL reading | Status |
|---|--:|---|---|---|
| `options_flow` | 34 | `sig.options_flow`, else param `options_positioning` | bias > 0 raises points above half-weight; < 0 lowers them | Active |
| `trend_price` | 24 | `_avg(ema_trend, price_action)` | same | Active |
| `unusual_activity` | 10 | `sig.unusual_activity`, else `unusual_options` | same | Active |
| `volume_pcr` | 8 | `sig.volume_pcr` | same | Active |
| `key_levels` | 8 | `sig.key_levels` | same | Active |
| `oi_change_expiry` | 6 | `_avg(daily_oi_change, flow_by_expiry)` | same | Active |
| `event_radar` | 5 | `sig.event_radar` | same | Active |
| `rsi_momentum` | 3 | `sig.rsi` | same | Active |
| `disparity` | 2 | `sig.disparity`, else param `disparity` | same | Active |
| `volatility_em` | **0** | `_avg(implied_volatility, expected_move)` | contributes 0 points | **Unused** (weight 0) |
Weights sum 100. BUY/SELL: one symmetric rule, points above/below 50 (B0). Timeframe: daily bars + options snapshots, no intraday bars.

### B3. Kimi Intraday (weights L174-185; code identical to Grok, `kimi_intraday_service.py` L42-71, differences only in weights and docstring)
`options_flow` 28, `trend_price` 23, `unusual_activity` 13, `volume_pcr` 11, `key_levels` 9, `oi_change_expiry` 7, `event_radar` 4, `rsi_momentum` 3, `disparity` 2, `volatility_em` 0 (Unused). Sum 100. Formulas as Grok.

### B4. Meta Intraday (weights L188-197; `meta_intraday_service.py` L39-66)
`options_flow` 25 (as Grok), `ema_trend_price` 20 (`_avg(ema_trend, price_action)`), `unusual_activity` 15, `key_levels` 12, `volume_pcr` 10, `daily_oi_change` 8 (`sig.daily_oi_change` only), `event_radar` 5, `rsi_momentum` 5. Sum 100. No `disparity`, no volatility. The `board()` docstring (L97) says "7-signal model"; the module header and `SOURCE` say 8 and the code defines 8 (VERIFIED-CODE).

### B5. Gemini Intraday (weights L202-214; `gemini_intraday_service.py` L37-68)
| Parameter | Wt | Bias formula |
|---|--:|---|
| `options_flow` | 13 | as Grok |
| `unusual_activity` | 12 | as Grok |
| `disparity` | 9 | `sig.disparity`, else param `disparity` |
| `volume_pcr` | 8 | `sig.volume_pcr` |
| `key_levels` | 7 | `sig.key_levels` |
| `oi_change` | 8 | `_avg(daily_oi_change, flow_by_expiry, oi_positioning)` |
| `ema_trend` | 11 | `sig.ema_trend` |
| `rsi_price` | 10 | `_avg(rsi, price_action)` |
| `insider_ownership` | 10 | `_avg(insider_activity, param short_insider)` (SEC filings, slow data) |
| `earnings_catalyst` | 10 | `_avg(earnings_results, merger_activity, funding_activity)` (SEC / Benzinga, slow data) |
| `dividends_event` | 2 | `_avg(dividend_trend, event_radar)` |
Sum 100. The docstring says an "80/20 market/company" split; the code's weights give 78 market / 22 company (13+12+9+8+7+8+11+10 vs 10+10+2). IV/expected move is not used for direction; `move_size` is reported only when a `trade` payload is passed, and `board()` never passes one.

### B6. Claude Pro Intraday (`claude_pro_intraday_service.py` L30-40 BLOCKS, L42-98 live scoring, L131-176 equity path)
Live path (phase `rth`/`post`): each block's bias = mean of its members' live-engine values, `clamp(value/max)`:
| Block | Wt | Live members (engine `intraday_model` params) | Equity path (`_EQ_SRC` L131-141) |
|---|--:|---|---|
| `options_flow` | 25 | `flow`, `disparity` | `options_flow` else `options_positioning` |
| `price_structure` | 20 | `vwap_pos`, `ema_trend`, `orb` | `ema_trend` else `price_action` |
| `gamma_levels` | 15 | `levels` | `key_levels` |
| `volume_pc` | 12 | `rvol`, `pc_ratio` | `volume_pcr` |
| `market_context` | 10 | `market`, `sector` (+ market breadth added to the average) | market breadth only |
| `unusual_activity` | 8 | `unusual` | `unusual_activity` else `unusual_options` |
| `momentum` | 5 | `rsi` (docstring says "RSI / VWAP slope"; only `rsi` is listed) | `rsi` |
| `premarket_bias` | 5 | `gap`, `coi` (**`coi` is not an engine parameter**, so only `gap` ever has data) | none (always no data) |
Sum 100. Engine member formulas are in B8. The docstring states the engine's trading gates are deliberately not applied.

### B7. ChatGPT Intraday (`chatgpt_intraday_service.py` L28-49; full 20-row table in `TRDGO_CHATGPT_INTRADAY_AUDIT.md` section 2)
20 parameters totalling 100. Always no data (weight 36): `market_structure` 10, `liquidity_sweeps` 8, `absorption` 6, `volume_profile` 5, `supply_demand` 4, `breakout_quality` 2, `volatility` 1. Detail-view-only (never on the board): `order_flow` 9 on the live path. Others map to engine params (`flow`, `vwap_pos`, `rvol`, `rel_strength`, `levels`/gamma, `orb`, `unusual`, `rsi`, `news`, `market`) or equity signals (`daily_oi_change`, breadth). Pre-market path maps 10 parameters (weight 52) via `_EQ_SRC` L157-168.

### B8. Live engine member formulas (used by ChatGPT and Claude Pro in `rth`/`post`; `intraday_model.py@36094e8`, values signed within +/- max)
`MAX` L46-51: market 10, sector 5, vwap_pos 10, ema_trend 8, orb 7, rsi 5, rvol 5, rel_strength 5, flow 12, unusual 8, pc_ratio 5, levels 5, disparity 5, gap 5, news 5 (sum 100).
* `market` L162: SPY and QQQ each +1 (price > VWAP and EMA9 > EMA21) / -1 (below both) / 0; average x 10.
* `sector` L173: sector ETF above VWAP and up today +5, below and down -5, else 0.
* `vwap_pos` L212: above VWAP: +10 if VWAP slope > 0.0005 else +5; below: -10 if slope < -0.0005 else -5.
* `ema_trend` L226: EMA9>EMA21 and price>EMA9: +8; reverse: -8; else +/-4 by EMA order (5-minute closes).
* `orb` L238: price above opening-range high (09:30-10:00 ET) with RVOL >= 1: +7; below the low: -7; else 0.
* `rsi` L251: RSI(14, 5-min): > 75 -> 0; 55-75 -> +5; 50-55 -> +2; < 25 -> 0; 25-45 -> -5; 45-50 -> -2 (L251-268, thresholds `_D` L29-40).
* `rvol` L269: sign(today's move) x clamp((RVOL-1)/(2-1), 0, 1) x 5.
* `rel_strength` L282: `clamp((stock %chg - SPY %chg)/1.0) x 5`.
* `flow` L308: `clamp((net call premium - net put premium)/20-day normaliser) x 12`, today's row only.
* `pc_ratio` L322: put/call < 0.6 +5; 0.6-1.0 -> 0; >= 1.3 -5; between 1.0 and 1.3 linear to -5.
* `disparity` L342: `(call_vol - put_vol)/(call_vol + put_vol) x 5`.
* `unusual` L354/L410: `clamp(net/1,000,000) x 8`, net = ask-side premium (calls +, puts -) of prints executed today with expiry 0-14 days, min premium $50,000 per print (`_unusual` L397-407).
* `levels` L357/L480: +5 crossed above largest-OI call wall today; -5 crossed below put wall; +2.5 bouncing within 0.5% of the put wall; 0 within 0.3% of max pain.
* `gap` L521: gap > +0.3%: +5 if holding above the pre-market high else -5; gap < -0.3%: -5 if below the open else +2.5; else 0.
* `news` L542/L552: `clamp((positive - negative headlines today)/2) x 5`.
Engine gates G1-G7 (L672-712: coverage >= 70, SPY above/below VWAP, confluence, group agreement, earnings today/tomorrow, liquidity >= $10 and 1M average volume) are computed by the engine but **not read by ChatGPT or Claude Pro**.

---
## Report C: exact scoring formulas and thresholds
* All six: `score = sum(weight_i x (0.5 + 0.5 x quant(bias_i)))`, missing bias = 0.5 x weight (B0).
* ChatGPT, Claude Pro: score computed from the live path or the equity path depending on `market_phase()` (`pre`/`closed` -> equity; `rth`/`post` -> engine cache `im2:<SYM>`), switch at `chatgpt_intraday_service.py` L251-252, `claude_pro_intraday_service.py` L226.
* Grok, Kimi, Meta, Gemini: always the equity path (no phase switch).
* BUY and SELL use **the same, symmetric rule** (>= 55 / <= 45); nothing differs by direction in scoring.
* Missing data contributes 50% of the weight (VERIFIED-CODE); neutral readings contribute 50% as well.
* **Not every weight is connected to data:** ChatGPT 36 of 100 never; Claude Pro `premarket_bias` partly (`coi`) and fully on the equity path (5); Grok/Kimi `volatility_em` has weight 0; Meta and Gemini: every weight has a source signal (individual signals may be missing at run time).
* Arithmetic fact: with all other inputs neutral, one fully bullish reading of weight w adds `0.5 w` points; reaching 55 needs +5.0 net (bearish: -5.0). E.g. Grok `options_flow` (34) alone at +1 gives 50 + 17 = 67 (BUY); at +0.5 gives 58.5 (BUY); Kimi 28: +0.5 gives 57.0.
* Mandatory confirmation: **none** in production for any board (no multi-bar, 15-minute or 2-scan confirmation). The 2-scan (>= 3 min apart) confirmation exists only inside the live engine's own signal state machine (`intraday_signal_v2.py` header L4-6, step at L285), which does not feed the paper trades.
* Coverage / agreement: Grok, Kimi, Meta, Gemini compute `coverage`, `agreement` and `low_confidence` (min coverage 0.50, min agreement 0.55, `tab_settings.py` L62) but the tracker's `_row_map` (L121-131) copies only signal, score, entry, now, sl, tp, so **`low_confidence` does not stop a trade**.

---
## Report D: timeframe and confirmation logic

| Strategy | Timeframes actually used | Updates | Data | Incomplete candle |
|---|---|---|---|---|
| ChatGPT / Claude Pro, `rth` and `post` | 5-minute bars (session VWAP, EMA9/21, RSI14, RVOL, opening range 09:30-10:00), daily bars for ATR and liquidity, today's options tape | engine score cached 45 s (`intraday_model.score` L630); bars cached 30 s with last-good reuse up to 300 s (`bars_meta` L106-131) | live provider calls, cached | the newest 5-minute bar is used as returned (`last_price` takes the last bar; RSI uses all closes); no filter removes a still-forming bar. Whether the provider returns a forming bar: `NOT VERIFIABLE` |
| ChatGPT / Claude Pro, `pre`/`closed` | daily bars + option-chain and flow snapshots | board cached 120 s; underlying snapshot held up to 48 h, age unchecked | cached, possibly delayed | n/a |
| Grok, Kimi, Meta, Gemini (all day) | daily bars (EMA/RSI/price action) + option-chain and flow snapshots + OI + filings/events | same cached snapshots | cached | n/a |
* No board uses more than one timeframe for a decision; entry signals come from the board score only. There is no confirmation step in production. `market_phase` (`intraday_indicators.py` L88-101): `pre` before 09:30, `rth` 09:30-16:00, `post` 16:00-20:00, `closed` overnight/weekends; holidays are not detected.
* The tracker polls the boards every 60 s, so a signal can open a trade within one minute of the score crossing 55/45.

---
## Report E: automatic entry and exit workflow (production, `demo_trade_service.py@36094e8`)
1. **Trigger:** thread `demo-trade-tracker` every 60 s: `if dts.should_tick(): dts.tick(force=True)` (`main.py` L521-540). `should_tick` L79-85: weekdays 04:00-20:00 ET, or after 16:00 ET while any trade is OPEN. No holiday calendar.
2. **Boards:** `tick` L193+ calls each board in `MODELS` L24-31 and flattens rows with `_row_map` L121-131.
3. **Entry condition** (L236-255): signal BUY or SELL and `entry` price present and no existing row today for this `(strategy, symbol)`. Direction: BUY -> LONG, SELL -> SHORT (L244).
4. **Quantity:** fixed `shares=100` (L248). No sizing from ATR, equity or risk.
5. **Duplicate prevention:** the `have` set (today's rows per strategy+symbol) and DB unique constraint `uq_demo_strategy_symbol_day` (`models_demo_trades.py` L25). Six strategies may hold the same symbol and side simultaneously (up to 6 x 38 = 228).
6. **What does not prevent entry:** stale inputs, low coverage, `low_confidence`, an earnings date, low liquidity, market direction, ETF vs stock, number of open positions, exchange holidays, pre-market quotes. `VERIFIED-CODE`.
7. **Stop/target:** board levels (+/-0.5 / 1.0 x ATR), else 1% / 2% defaults (`_with_levels` L183-190).
8. **Monitoring:** each tick re-reads OPEN rows with `session_date = today` (L214); last price = board `now` or UW quote (cached 120 s).
9. **Exits** (`_exit_reason` L141-153, `_exit_timed` L88-96): `STOP`, `TARGET`, `SCORE_EXIT` (board score moves 15 points against the entry score, `SCORE_DROP_EXIT` L20), `TIMED_EXIT` at/after 16:00 ET for trades entered before 16:00. Fill price: target at target, stop at stop, otherwise last price (L222-227).
10. **Closed within the day?** Trades entered before 16:00: yes at the first tick after 16:00. Trades entered 16:00-20:00: not by the timed rule. Rows left from an earlier day: never loaded (L214), so never closed by the manager. Stale signals can open trades (item 6).

---
## Report F: actual intraday trading records
* **Table:** `demo_trades`, one row per trade; stored fields: strategy, symbol, side, shares, session_date, entry_time/price, stop, target, **score_at_entry**, last_price, profit, profit_pct, status, exit_time/price (`models_demo_trades.py` L30-50). **No parameter readings, no reason text, no data ages are stored** (`VERIFIED-CODE`). "Entry reason" and "parameter readings at entry" are therefore `NOT VERIFIABLE` for every trade; I did not reconstruct them from current data.
* **Aggregate evidence (VERIFIED-RUNTIME, all strategies together, 06:59 ET):** all-history statuses OPEN 172, SCORE_EXIT 122, STOP 246, TARGET 64, TIMED_EXIT 200; last entry 04:00:25 ET; last exit 04:03:27 ET; no entry in the last 15 min; all 172 open rows opened before 16:00 ET; none carried; none older than 30 h. You reported they belong to six strategies, opened at 04:00 ET.
* **Per strategy (total, open, closed, BUY/SELL, entry times, entry scores, stop/target, exit reasons, realized and unrealized P&L): `NOT VERIFIABLE` from here.** The read-only script `docs/handover_2026-10-27/phaseB/phaseB_chatgpt_trades_evidence.ps1` (section 1b: every strategy, all history; section 1: today; sections 3-5: per-trade lines for one strategy) will produce them. It is syntax-checked only and has not been run.

---
## Report G: comparison

| | ChatGPT | Claude Pro | Grok | Kimi | Meta | Gemini |
|---|---|---|---|---|---|---|
| Parameters | 20 | 8 blocks | 10 | 10 | 8 | 11 |
| Weight on options-flow-type signals | 10 (+9 order flow detail-only) | 25 | 34 | 28 | 25 | 13 |
| Weight on trend/price | 9 vwap/ema + 4 orb + 7 rel strength | 20 | 24 | 23 | 20 | 11 + 10 (rsi/price) |
| Zero-weight or always-empty weight | 36 | 5 (+ part of `momentum`'s docstring) | 0 (`volatility_em` = 0) | 0 | none | none |
| Equity-path vs live-engine | both (phase switch) | both (phase switch) | equity only | equity only | equity only | equity only |
| Company/slow data in the score | catalyst only | none | none | none | none | insider 10, earnings/M&A/funding 10, dividends 2 |
| Cutoffs | 55 / 45 | 55 / 45 | 55 / 45 | 55 / 45 | 55 / 45 | 55 / 45 |
| Quantisation, missing data | same | same | same | same | same | same |
| Levels (stop/target) | +/-0.5/1.0 ATR | same | same | same | same | same |
| Tracker entry/exit rules | shared | shared | shared | shared | shared | shared |
| Missing-data handling | 50% of weight | 50% | 50% | 50% | 50% | 50% |
| Data providers | UW bars/options/news + chain + Benzinga/Marketaux | same | UW + chain + daily bars + Benzinga/Marketaux | same | same | + SEC EDGAR, Nasdaq, UW screener |

**Do they use genuinely different logic?** Mostly no (VERIFIED-CODE):
* Grok and Kimi are **the same code** with two weight tables.
* Meta is the same pipeline with 8 of Grok's signals and its own weights.
* Gemini uses the same pipeline with a different grouping and extra company, filing and dividend signals.
* ChatGPT and Claude Pro are different in one respect: during `rth`/`post` they score from the live 5-minute engine parameters; before the open they fall back to the same equity signals as the other four.
* All six share one quantisation, one cutoff pair, one stop/target rule and one tracker. Differences are weights, groupings, and (for two strategies) the intraday data in the live path.

---
## Report H: missing, inactive or unverified functionality
| Item | Class |
|---|---|
| Per-strategy trade counts, P&L, entry times | NOT VERIFIABLE (script prepared, not run) |
| Parameter readings and reasons at entry | NOT VERIFIABLE (not recorded) |
| Age of the cached snapshots used at entry | NOT VERIFIABLE (no age check; `peek` ignores age) |
| `INTRADAY_MODEL` effective value | NOT VERIFIABLE |
| Live-path behaviour of ChatGPT/Claude Pro during 09:30-20:00 on the VPS | NOT VERIFIABLE (the 04:00 batch came from the pre-market path, INFERRED) |
| ChatGPT: 7 parameters with no source, `order_flow` live path | VERIFIED-CODE: unused / detail-only |
| Claude Pro `premarket_bias` member `coi` | VERIFIED-CODE: no such engine parameter |
| Grok / Kimi `volatility_em` | VERIFIED-CODE: weight 0, unused |
| `low_confidence` flag | VERIFIED-CODE: computed, never used by the tracker |
| Engine gates G1-G7 | VERIFIED-CODE: computed, not used by ChatGPT/Claude Pro |
| Docstring vs code differences | Gemini "80/20" is 78/22; Meta `board()` docstring says 7 signals but 8 exist; Claude Pro momentum "RSI / VWAP slope" is RSI only |

### Production vs newer release (what runs vs what is only developed)
* **Running (VPS, `36094e8`):** everything in Reports A-F.
* **Developed, not running (`277f30f`, VERIFIED-CODE):** `intraday_safety.gate` on all six boards (per-signal freshness limits `INTRADAY_FRESHNESS` `tab_settings.py` L411-421; coverage >= max(30%, 75% of attainable); live price age <= 300 s; API-capacity check; WAIT instead of a signal when any fails); a mandatory 15-minute closed-candle confirmation (`INTRADAY_15M` L454-464: EMA 9/21, RSI 52/48, closed bars only) that the tracker asks again at entry; an entry lock; equity-only entries; gap-aware fills (`PAPER_EXECUTION` L438-442); carried-trade management; tiered live scanning. The boards' scoring formulas and weights are unchanged by the release (the diffs to the six services are only the gate and the response composition).

STOP. No action taken.
