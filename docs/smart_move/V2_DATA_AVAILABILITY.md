# Smart Move V2 (100 points): data availability audit — 2026-11-01

Evidence used: the repository's provider code, the committed OpenAPI spec `api_spec.yaml`, the saved payloads of the 122-stock run (`run_20261101_full_universe`), and the owner-verified plan notes (Unusual Whales Whale Bundle Advanced; 3 concurrent requests per token; daily quota unlimited per dashboard on 23 Oct). **No endpoint was called for this audit.** "READY" = the data is already saved or derivable from saved data. "PARTIAL" = an endpoint exists in the spec and the repo, but nothing has been collected or the direction is undefined. "BLOCKED" = no verified field. "UNVERIFIED" is applied to anything about subscription and limits that needs the dashboard.

| Parameter (pts) | Source / endpoint | Fields used | History | Pre-announcement / point-in-time | Calls per stock | Status |
|---|---|---|---|---|---|---|
| F3 reactions (15) | UW `/api/earnings/{t}?limit=24`, `/ohlc/1d` | `post_earnings_move_1d`, `report_time`, bars | 24 quarters | yes (past rows only) | 2 | **READY** (saved) |
| F5 price/volume (10) | UW `/api/stock/{t}/ohlc/1d` | OHLCV regular session | 3Y | yes, last completed session | (shared above) | **READY** |
| F7 volatility (10) | UW `/volatility/context`, `/iv-rank` | skew label | none (live only) | only if captured inside the window | 2 | **READY** (live in window; 116/122 captured) |
| F8 sector (5) | UW ohlc SPY + sector ETF | closes | 3Y | yes | shared (~15/day) | **READY** |
| Analyst EPS/revenue & revisions (15) | UW `/api/screener/analysts` (rating actions, `timestamp`); `/api/companies/{t}/earnings-estimates` (current estimates) | action, timestamp, target | actions: dated; estimates: current only | rating actions yes; estimate **revisions need dated snapshots that were never saved** | 1-2 | **PARTIAL** (rating actions only; never collected) |
| Options flow (15) | UW `/api/stock/{t}/net-prem-ticks`, `/flow-alerts` | `net_call_premium`, `net_put_premium`, `has_sweep`, `total_premium`, `created_at` | 2-year lookback per plan | yes (tick/alert timestamps) | 1-2 | **PARTIAL** (adapter + scorer built; never collected; endpoint used by production code but permission for this token not re-verified) |
| Implied vs historical move (10) | UW `/api/earnings/{t}` (same payload as F3) | `expected_move_perc`, `post_earnings_move_1d` | 24 quarters | yes | 0 extra | **PARTIAL**: the ratio is READY (115/122); a magnitude has **no defined direction**, so it adds evidence but no bull/bear points until the owner defines the mapping |
| Short interest & institutions (5) | UW `/api/shorts/{t}/interest-float/v2`, `/api/institution/{t}/ownership` | `si_float`, `market_date`; `units_change`, `filing_date` | yes | FINRA (~2 weeks) and 13F (45-day) delayed; dates present | 2 | **PARTIAL** (never collected; delayed data) |
| Relative strength (5) | derived from saved ohlc | 10-session return vs SPY and vs sector ETF | 3Y | yes | 0 extra | **READY**, but overlaps V1 F5/F8 (double-counting risk, documented) |
| Guidance, analyst changes, news (10) | UW `/api/news/headlines` (`sentiment`, `is_major`, `tags`) | headline metadata | recent | timestamped | 1 | **BLOCKED**: no structured guidance field; the meaning of `sentiment` is unverified; text classification would be invented |

## Result on the saved 122-stock run
* Maximum evidence any stock reaches in V2: **55 of 100 points** (F3 15 + F5 10 + F7 10 + F8 5 + implied-vs-hist 10 + relative strength 5). The minimum for a decision is 60. **Every V2 result is therefore NO TRADE (insufficient coverage).**
* Even with all four uncollected factors, the news/guidance factor (10 points) is BLOCKED, so the best case is 90 of 100.
* Directional capacity is lower than coverage: implied-vs-historical contributes no direction.

## Decisions the owner must make (not invented)
1. The direction (if any) of the implied-vs-historical ratio.
2. Thresholds for rating actions, flow premium, short interest and relative strength (current values are documented hypotheses in `CONFIG`, hashed, unvalidated).
3. How to treat news/guidance (define a structured field or drop the factor).
4. Authorization to collect analyst, flow and short/institutional data for the universe (about 5 extra requests per stock, roughly 600 for 122 stocks, one sequential run).

## Needs dashboard / support evidence (UNVERIFIED here)
Subscription status after the 1 Nov renewal; whether this token may call `net-prem-ticks`, `flow-alerts`, `screener/analysts`, `shorts/.../interest-float/v2`, `institution/.../ownership`, `news/headlines`; the 3-in-flight limit today.

---
## Update v2-exp-2 (rules made explicit; pipeline built; still no new live data)
| Factor | Rule now implemented (all thresholds in `CONFIG`, hashed, unvalidated) | Data it needs | Collected? |
|---|---|---|---|
| Analyst (15) | 10 pts: EPS and revenue consensus (`earnings-estimates`: `eps_estimate_average`, `revenue_estimate_average`) vs the earliest SAVED consensus 7-60 days older, +-1% each; both agree = full, one = half, conflict = 0. 5 pts: net rating upgrades minus downgrades (`screener/analysts`) in 30 days, net >= 2 | consensus snapshots over time (the endpoint returns only the current consensus, so history must be built by weekly collection) | no |
| Options flow (15) | Direction from the aggressor side: ask-side calls + bid-side puts (bullish) vs bid-side calls + ask-side puts (bearish) from `flow-alerts`; multi-leg excluded, expiry must be on/after the earnings date, volume/OI >= 1, sweeps x1.5, >= 3 alerts and >= $500k attributed premium; |net| >= 0.5 full, >= 0.25 half | `flow-alerts` rows with `created_at` <= capture | no |
| Implied vs historical move (10) | risk label only (rich/cheap/in line); **no direction, not counted toward the 60-point directional minimum** | saved `earnings` payload | yes (115/122) |
| Short/institutional (5) | 2.5: SI float >= 15% AND short interest up >= 10% between snapshots 10+ days apart = bearish (high alone or falling = no direction); 2.5: net 13F unit change >= +-0.5% of shares | two `interest-float/v2` snapshots; `institution/{t}/ownership` | no |
| Relative strength (5) | 5-session return vs SPY and sector ETF on the last COMMON session; +-2 points | saved ohlc | yes |
| News/guidance (10) | BLOCKED: `sentiment_semantics_verified=false`. When verified, only headlines stamped before BOTH capture and release count | `news/headlines` sample to verify the field | no |

**Directional minimum:** 60 of the points that CAN carry direction (everything except the implied-move factor). Today the most any stock reaches is **45** (F3, F5, F7, F8 + relative strength), so all 122 results are DATA INCOMPLETE. The bar was not lowered. With all new factors collected the maximum is 90; NEWS/GUIDANCE stays BLOCKED until its field is verified.

**Pipeline (`smart_move_v2_collect.py`)**: plan with de-duplication (same-day, 7-day estimates, 10-day short interest, 30-day ownership), requests only through `RefreshService._call` (governor, kill switch, 401/429 stop), response validation, append-only `v2_evidence.jsonl` with hashes, checkpoint resume, request accounting, as-of-capture reads. About 5 requests per stock (about 610 for 122 stocks), plus 1 more if the news sample is enabled; estimates and short interest need repeat weekly/10-day collections before they can score.
