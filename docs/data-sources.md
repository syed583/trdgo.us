# Data sources — what Tradgo.us pulls from each provider

Verified against the backend code (`unusualwhales_service.py`, `finnhub_service.py` and all `uw_*` modules).

---

## Unusual Whales — 51 endpoints
Base URL: `https://api.unusualwhales.com`

### Prices / quotes / candles
| Endpoint | Returns / used for |
|---|---|
| `GET /stock/{ticker}/quote` | Bid/ask/last — the core quote (every page, strip, earnings) |
| `GET /stock/{ticker}/ohlc/{size}` | OHLCV candles (1m/5m/15m/1h/1d) — VWAP, opening range, relative volume, technicals |
| `GET /stock/{ticker}/info` | Sector / description / market cap (company card) |

### Options flow / tape
| Endpoint | Returns / used for |
|---|---|
| `GET /stock/{ticker}/flow-alerts` | Per-ticker flow alerts |
| `GET /option-trades/flow-alerts` | Market-wide flow tape (NBBO/greeks/sweep) — live Flow screen |
| `GET /option-activity/unusual` | Unusual contracts — Disparity + flow params |
| `GET /stock/{ticker}/flow-recent` | Recent per-stock prints |
| `GET /stock/{ticker}/flow-per-expiry` | Flow by expiry |
| `GET /stock/{ticker}/flow-per-strike` | Flow by strike |
| `GET /options-pulse/top` | Busiest option names |
| `GET /options-pulse/sectors` | Options pulse by sector |
| `GET /option-contract/{occ}/intraday` | Single-contract intraday |
| `GET /option-contract/{occ}/flow` | Single-contract flow |
| `GET /option-contract/{occ}/volume-profile` | Single-contract volume profile |
| `GET /option-contract/{occ}/historic` | Single-contract history |

### Options chain / greeks
| Endpoint | Returns / used for |
|---|---|
| `GET /stock/{ticker}/option-chains` | Full chain (vol/OI/IV) |
| `GET /stock/{ticker}/option-contracts` | Per-contract quote/vol/OI/greeks — Options page, key levels, OI |
| `GET /stock/{ticker}/option/volume-oi-expiry` | Expiry list w/ vol+OI (front-expiry pick) |
| `GET /stock/{ticker}/greek-exposure` | Aggregate greek (GEX) exposure |
| `GET /stock/{ticker}/greek-exposure/strike` | Greek exposure by strike |
| `GET /stock/{ticker}/options-volume` | Day call/put volume, premium, OI |
| `GET /stock/{ticker}/net-prem-ticks` | Net call/put premium per minute |
| `GET /stock/{ticker}/max-pain` | Max pain |

### Open interest
| Endpoint | Returns / used for |
|---|---|
| `GET /stock/{ticker}/oi-change` | Overnight OI change per contract |
| `GET /market/oi-change` | Market-wide OI build-up |

### Volatility / IV
| Endpoint | Returns / used for |
|---|---|
| `GET /stock/{ticker}/iv-rank` | IV vs its own year |
| `GET /stock/{ticker}/volatility/realized` | Realized vol |
| `GET /stock/{ticker}/volatility/term-structure` | IV term structure |

### Market-wide flow / tide
| Endpoint | Returns / used for |
|---|---|
| `GET /market/market-tide` | Whole-market net call/put premium — Market Flow |
| `GET /market/{sector}/sector-tide` | Net premium per sector |
| `GET /market/total-options-volume` | Market call/put volume + premium |
| `GET /market/movers` | Top movers |
| `GET /market/insider-buy-sells` | Market-wide insider flow |

### Dark pool
| Endpoint | Returns / used for |
|---|---|
| `GET /darkpool/{ticker}` | Per-symbol off-exchange prints |
| `GET /darkpool/recent` | Market-wide recent prints |
| `GET /stock/{ticker}/stock-volume-price-levels` | Volume-by-price levels |

### Earnings
| Endpoint | Returns / used for |
|---|---|
| `GET /stock/{ticker}/earnings` | Reported quarters (est/actual/reaction) |
| `GET /earnings/{ticker}` | Per-ticker earnings history (calendar feed) |
| `GET /earnings/premarket` | Today's pre-market earnings slate |
| `GET /earnings/afterhours` | Today's after-hours earnings slate |
| `GET /companies/{ticker}/earnings-estimates` | Next-quarter analyst estimates |

### Fundamentals / dividends
| Endpoint | Returns / used for |
|---|---|
| `GET /stock/{ticker}/fundamental-breakdown` | Valuation / margins / growth |
| `GET /companies/{ticker}/dividends` | Declared dividends |

### Analyst / insider / institutional
| Endpoint | Returns / used for |
|---|---|
| `GET /screener/analysts` | Upgrades/downgrades/target changes |
| `GET /insider/{ticker}` | Officer/director trades |
| `GET /insider/transactions` | Insider transactions feed |
| `GET /insider/{sector}/sector-flow` | Insider flow by sector |
| `GET /institution/{ticker}/ownership` | Institutional holders + change |

### Short interest · Screener · News
| Endpoint | Returns / used for |
|---|---|
| `GET /shorts/{ticker}/interest-float/v2` | Short interest / float |
| `GET /screener/stocks` | Stock screener |
| `GET /news/headlines` | Market / ticker headlines |

*(Plus one startup key-check probe: `GET /stock/SPY/quote`.)*

---

## Finnhub — 6 endpoints (free plan only)
Base URL: `https://finnhub.io/api/v1`

| Endpoint | Returns / used for |
|---|---|
| `GET /stock/recommendation` | Analyst recommendation trend — Tradgo Call, earnings equity |
| `GET /stock/peers` | Peer tickers (max 8) — peer earnings compare, Tradgo Call |
| `GET /stock/insider-sentiment` | Monthly insider sentiment (180-day) — Tradgo Call |
| `GET /company-news` | Company news list (7-day) — Tradgo Call news signal |
| `GET /stock/earnings` | Latest reported quarter: EPS actual vs est + surprise — earnings equity |
| `GET /calendar/earnings` | Next scheduled earnings date — Tradgo Call |

**Deliberately not used** (premium-gated): candles, news-sentiment, economic/IPO calendar.
**Defined but unused:** `GET /stock/metric` (`metrics()` — no caller; removable dead code).
