# Task 1 — why MT5 does not show 10% of the software's P&L

Read-only. Nothing was changed. Data pulled from the owner's signed-in session at
2026-11-21 ~12:05 Dubai, before the 12:25 quiet period.

## What I could and could not read

The owner's account (`918433967036`) is `role: user`, `mt5_operator: false`. **Every MT5 endpoint
returns 403** — `/api/mt5/status`, `/settings`, `/symbols`, `/audit`: *"MT5 auto-trading is for the
operator account only."* So the MT5 half of the comparison is not available from this session, and
the per-trade table the owner asked for (sent? rejected? delay? slippage? close reason?) cannot be
built yet. What follows is the software half, which is complete, plus what it already proves.

## The software side, 5 trading days (16–20 Nov 2026)

    day        trades   realised
    2026-11-16   120     -621.33
    2026-11-17   107     +893.71
    2026-11-18   128     +937.46
    2026-11-19   111    +2072.91
    2026-11-20   118    +4128.84      <- the "+$4,000" the owner is quoting is ONE DAY
    -------------------------------
    total        584    +7411.59      39 distinct symbols

    by exit reason      n     $
      Target           68   +10092.84
      Score exit      429     +599.35
      Closed at bell   63      +61.03
      Stopped          24    -3341.63

    by model: Agent 1 +2242.36 (155) | Agent 2 +2374.45 (148)
              Agent 3 +2092.86 (153) | Agent 4  +701.92 (128)

## The sizing finding — and it does NOT explain the shortfall

The software sizes by DOLLAR NOTIONAL: median notional $9,917, so shares = ~10,000 / price.
Observed share counts run from **6 to 733** per trade. MT5 sends a FIXED SHARE COUNT per trade
(A.21's `MT5_SHARES_PER_TRADE`, 10).

A fixed share count against a dollar-notional book is not a scale factor at all — it is a
different book. The ratio is 10/shares, which varies per symbol by a factor of a hundred:

    GS    avg price $865, avg  11 shares -> a 10-share mirror is 0.91x the software, not 0.1x
    SPY   avg price $730, avg  13 shares -> 0.77x
    PCG   avg price $13.75, avg 726 shares -> 0.014x

Totals over the 584 trades:

    software                                  +7411.59
    what the owner expects at a true 0.1x      +741.16
    what a FIXED 10-share mirror makes        +3202.90

**So sizing cannot be the reason MT5 is short.** A fixed-10 mirror would make about FOUR TIMES the
0.1x expectation, not less — because the profit is concentrated in high-priced names (GS, SPY,
QQQ, UNH, JPM) where 10 shares is close to full software size. If MT5 is showing far less than
+$400 on the +$4,128 day, the trades are not reaching MT5 at all, or not at these prices.

## The leading hypothesis, to be confirmed against MT5 data

Auto-trading was **PAUSED on 2026-11-12** (`AUTO_TRADE`), and I have no evidence it was resumed.
If it is still paused, **no signal from 16–20 Nov was ever sent** and the whole gap is that one
switch. The A.18 exclusion list (12 symbols) and any unmapped symbols would come after it.

This is a hypothesis. It needs `/api/mt5/status` and `/api/mt5/audit`, which this session cannot
read.

## What is needed to finish

Either the operator account signed in to the browser pane, or these four pasted in:

    /api/mt5/status          is auto-trade on or paused; account; equity; connection
    /api/mt5/settings        shares per trade, daily loss cap, dry-run flag, max positions
    /api/mt5/symbols         mapping and the exclusion list
    /api/mt5/audit?limit=400 the event log: sends, fills, rejects and their reasons

With those the full per-trade table and the dollar bridge can be built; the software half is
already computed and saved here.

## Two more facts from the software's own rules block (same pull)

    notional_usd   10000        confirmed: the book is $10,000 per trade, not a share count
    fill_model     "observed"   the paper book fills at the observed price - no spread crossed,
                                no slippage, no commission. Every one of those is a real cost on
                                MT5 and belongs in the bridge as a separate line.
    max_stop_pct   2.5          stop_cap_hits 5,756 - a large number of signals had their stop
                                capped, which changes risk per trade and may change what MT5 was
                                asked to do.
    source         "Strategy Trades - $10,000-notional paper trades from the intraday signals"

`fill_model: observed` matters for the bridge: the software's +$7,411 is a frictionless number.
Even a perfectly mirrored MT5 book would come in under it by the spread (median spread 0.04% of
notional, ~$4 per $10,000 trade, 584 trades = roughly $230 of spread at full size, ~$23 at 0.1x)
plus commission and any swap.

## Status: BLOCKED on MT5 read access. Nothing changed; VPS reads stopped at 12:07 Dubai.
