# Kit Q - live audit of all 23 pages, and the fixes (2026-11-22/23)

Audited on **tradgo.us, signed in**, at 1051px. Not against a fixture: the owner signed in to the
browser pane and every number below was measured on the real site.

## The detector

Four things, on every page in the user menu:

| check | what it means |
|---|---|
| `FIXED-H-OVERFLOW` | a box whose content needs more height than the box has, with nothing clipping it |
| `H-SPILL` | content wider than its box, not scrollable |
| `OFF-RIGHT` | an element whose right edge is past the window, with no scrollable ancestor |
| `OVERLAP` | two in-flow siblings drawn on top of each other |

**The detector lies in two ways, and both mattered.**

1. `getComputedStyle().height` returns the USED value in px even when the CSS says `auto`, so
   `FIXED-H-OVERFLOW` cannot by itself prove a height was pinned. The reliable half of that signal
   is `scrollHeight > clientHeight`.
2. It cannot tell a bug from a deliberate overlay. **Three findings were false positives** and were
   left alone after checking the source:
   - `.nal-gauge-txt` has `margin-top: -34px` on purpose, to sit the number inside the donut.
   - `.mo-explain` has `margin: -70px 0 0 559px` on purpose, to sit beside the index row.
   - `.mo-legend` / `.hint` do not overlap at all when measured directly (560-606 then 614-633).

Anything flagged was read in the stylesheet before it was touched.

## Result

**15 of 23 pages were clean.** Findings 24 -> 3, and all three remaining are accounted for.

| page | before | after |
|---|---|---|
| Smart Money (LIVE) | 8 | **0** |
| News & Sentiment | 1 | **0** |
| Market Overview | 3 | 0 real (2 false positives) |
| Strategy Trades | 3 | 0 real |
| Analysis Intraday | 8 | 1 real + 1 false positive |
| Watchlist | 1 | **0** |

Clean throughout: Earnings Calendar, IV Crush Check, Unusual Activity, Pre-Earnings Direction,
Signal History, Forward Scoreboard, Historical Backtest, Post-Earnings AI, Overview, Stocks,
Options, OI Build-Up, Volatility, Dark Pool, Market Insiders, My Broker.

## One root cause, and it is mine

Building the Figma parity sheet in kit N I copied the frame's numbers **literally** - heights AND
widths. A frame is one drawing of one state, so its height is whatever content happened to be in it
that day. The moment the real page has more, the box stays pinned, `overflow: visible` lets the
extra escape, and everything after it is laid out as though the box were still short. That is the
overlap the owner reported.

Measured:

```
.sm-summary      box  90px, content 190px   -> the NO ACCURACY CLAIM banner landed on the cards
.sm-filters      box  40px, content  96px
.mo-head         box  51px, content 115px
.nnw/.nua-head   box  61px, content  65px
.nal-strip       needed 933px in an 808px box -> VIX sliced at the page edge
.nal-feats       left 536px + width 311px -> right edge 847px, off the page by 43px
.nd-hero-left    width 499px, containing an h1 the frame sets to 520px
.nal-controls    560px wide inside that 499px column
```

Note the hero: **the frame draws the headline wider than the box it sits in**. The design file
disagrees with itself, as it has before. The container wins; the headline wraps ~21px earlier than
the drawing shows.

## What fixed it - three passes, each measured

1. **Pinned heights became minimums.** Removed the Smart Money overlap entirely (8 -> 0).
2. **Grids got `auto-fit` + `minmax`.** This alone did almost nothing, which was the useful
   surprise: a `1fr` track is `minmax(AUTO, 1fr)` and a flex item defaults to `min-width: auto` -
   both mean *never narrower than my content*. Widening the track cannot help when the floor is the
   content. **`min-width: 0` on the CHILDREN** is what lets them shrink.
3. **Once they could shrink, they wrapped - and hit more pinned heights** (the 67px index card, the
   39px tab row). Those had to give too.

Figma literals (`left: 536px`, `width: 311px`, `width: 499px`) became `min()` of the frame's number
and the space available, so the desktop layout the owner judged is unchanged pixel for pixel and
nothing leaves the page at narrower widths.

## Also fixed - three things kit P got wrong

Every one found by the owner clicking, not by a test:

1. **A flat `$0.00` was painted GREEN.** The colour rule read `>= 0`, so zero showed in the profit
   colour and a day with no trades read as gains. Zero now gets no colour, in tiles and table.
2. **Four tiles in a three-column grid** left the fourth stranded on its own row.
3. **"A bid of zero is not a bid" was fixed on the Watchlist page only.** The Analysis Intraday
   quote card has its own copy and still printed `0.00` out of hours.

## Found, NOT fixed

These are real and still open:

- **The polling storm.** One idle tab made **190 API calls** - sparklines x26, pulse/movers/quote
  x16 each, health x13 - for 49s of cumulative fetch. This is the lag the owner feels, and it burns
  provider budget every minute a tab is open. Page render is fine: 219ms to interactive, 740 nodes.
- **Market sentiment reads 100/100 "Extreme greed" off ONE component over 6 names**, while
  describing itself as a composite of breadth, volatility and options. VIX is `NO_DATA`, so the
  volatility leg is simply gone and what survives pegs the gauge at maximum.
- **NVDA at two prices, opposite signs, on one screen**: movers 127.53/-12.64% vs quote
  130.01/+3.57% (previous close 125.53 - so 127.53 would be +1.59%).
- **Dow Jones and VIX return `NO_DATA`**; Russell 2000 returns a price with a null change and never
  renders.
- **The "Try the demo" button does nothing** - it sets `?demo=1` and stops.
- **Top Movers is down to 4 rows** - a consequence of kit P's ETF filter (14 dropped, 2 below the
  price floor). Working as designed, but thin.
- **Strategy Trades has no cumulative totals.** `/api/strategy-trades` is date-scoped only; six
  likely endpoints for running totals do not exist. The owner's "total closed / total profit" cards
  need a new read-only backend aggregate - awaiting his choice of all-time vs 30 days.
