# Kit P — live click test and fixes

Real clicks in a browser on the live site, not DOM probes. One page at a time, fixes added to
kit P before moving on. Notes updated after each page so an interruption loses nothing.

Signed in as `syed waquas`, `role: user`, `isAdmin false` — so this is the NON-ADMIN pass.
The admin pass is blocked until the owner signs that account into the browser pane.

## Order (owner, 2026-11-21)

1. Analysis Intraday  2. Strategy Trades  3. Smart Money Move  4. Watchlist
5. News & Sentiment  6. Market Overview  7. the rest of the menu, top to bottom

## Already decided for kit P

- **Headphones/Help icon: REMOVE** from the top bar. Help moves inside the profile menu.
- **Bell: rebuild as a dropdown panel** — buy/sell trade alerts (symbol, side, price, time), a
  sound when a new one arrives, and a mute toggle inside the panel. Today it navigates to
  `/alerts`, which is a rule builder, not a feed.
- **Sidebar promo** moves INSIDE the scrolling nav, at the very end, so every menu item is
  reachable first. Today it is pinned below the nav and always visible.
- **Chat footer leak**: the assistant panel ends with "powered by Claude on your UW data" — a model
  name and a vendor name in front of every user. Found by clicking, not by any probe.

## Top bar — tested 2026-11-21, real clicks by element reference

| control | result |
|---|---|
| Bell "Alerts" | navigates to `/alerts` (rule builder: Ticker/Condition/Threshold/Create, "Rules (0)"). No panel, no trade alerts, no sound, no mute. |
| Assistant (bot) | **works** — panel opens, grounded on the current symbol |
| Assistant chat | **works** — asked for NVDA key levels, got a real answer (GEX -99M, put/call 0.46, support 130-133, resistance 140-145) |
| Headphones | it is **Help**, and it navigates to `/settings`. There is no sound icon, so there is nothing to merge. |
| Theme toggle | not yet clicked |
| Profile chip | not yet clicked |

## Page 1 - Analysis Intraday - DONE (non-admin, dark)

37 interactive controls enumerated and exercised. Clicks dispatched on the real elements in the
live page; navigation verified by where it actually landed and what the page then said.

| control | result |
|---------|--------|
| Horizon chips Today / Tomorrow / Swing | WORK - the active chip and the basis line follow |
| Ticker box + Analyze | works (see Try chips) |
| Try chips AAPL/TSLA/MSFT/AMZN/META | WORK - start an analysis |
| Hero card -> Earnings | OK -> /earnings-calendar, h1 "Earnings Calendar" |
| Hero card -> Options Flow | OK -> /unusual-activity, h1 "Unusual Activity" |
| Hero card -> Strategy Trades | OK -> /strategy-trades, h1 "Strategy Trades" |
| Hero card -> Smart Money Move | OK -> /smart-move/engine20 |
| Top Movers tabs Most Active / Gainers / Losers | WORK - the list changes each time |
| Top Movers row click | WORKS - runs the analysis and returns a full result |
| Featured range tabs 1D / 5D / 1M | WORK - 5D and 1M change the chart DATA, not just the button |
| Earnings calendar tabs Today / Tomorrow / This week | WORK - This week shows 6 rows |
| "View all" (options flow) | OK -> /unusual-activity |
| "View all" (sector) | OK -> /market |
| "Analyse NVDA ->" | link present, /ai-insights?symbol=NVDA |

### Defects on this page

1. **Top Movers lists leveraged and inverse ETFs** - SOXL, SPDN, TQQQ, IREN, SNDK appear in what
   reads as a stock movers list. Kit N's floor (price >= $5, market cap >= $2B) does not exclude
   them because the feed carries no market cap for an ETF, so the cap test cannot fail. Needs an
   explicit issue-type filter, not a bigger number.
2. **Assistant chat footer leaks a vendor and a model name**: "Analysis only - not financial advice
   - powered by Claude on your UW data". Every user sees it. Found by CLICKING; no probe would
   have.

### One measurement of mine that was wrong, recorded so it is not repeated

I first reported the Top Movers row as doing nothing, on the strength of body.innerText shrinking
from 2115 to 1457 characters. That was a bad proxy: the analysed view is denser, not emptier. A
screenshot showed a complete result (SELL, score 35/100, confidence 69%, key insights). Character
counts do not tell you whether a page works.

