# Capturing real production payloads for the side-by-side pairs

## Why this exists

The owner asked for the five pages to be re-checked "against the LIVE server ... with live data".
The live server cannot show them: it runs kit M rebuild `bf495623`, which has none of this work.
Checked from the owner's own signed-in session on 2026-11-20 at 09:10 ET:

| endpoint | live server | note |
|---|---|---|
| `/api/market/pulse` | **200, real** | `sentiment` = `{status, value, label, components, detail}`, `value: 41`, `label: "Neutral"` |
| `/api/movers?limit=5` | **200, real** | `floor` absent — the price / market-cap floor is not deployed |
| `/api/options/iv-history/NVDA` | **200, real** | deployed |
| `/api/landing/featured` | falls through to `index.html` | **not deployed** |
| `/api/landing/sparklines` | falls through to `index.html` | **not deployed** |

So "a live pair of the new design" is circular: the design has to be installed before the live
server can render it, and the owner wants to approve the pairs before the install kit.

## What this does instead

It reads the REAL production payloads through the owner's already-signed-in browser pane — no
credential is handled, nothing is sent anywhere, and the calls are plain same-origin GETs the page
makes anyway — writes them to `_live_payloads.json`, and the local fixture replays them verbatim.

The result is the new design rendered against the figures production actually returned, with the
capture time stamped on the pair. That is not the same as the live server and the pairs say so.

## What it is not

It is not a live-server screenshot and it must never be presented as one. Anything that depends on
a route the live server does not have — the featured symbol's chart and technical tiles, every
sparkline — still comes from the fixture, and those are listed on each pair.
