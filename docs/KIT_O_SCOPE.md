# Kit O — scope and the live defect sweep

Built, not installed. Progress notes kept here so an interruption never loses the state.

## Status

- [x] Live read-only sweep of 9 real routes, both themes, as a NON-ADMIN account
- [ ] 1. Analysis Intraday: remove the 3-box statistics row, close the gap
- [ ] 2. Smart Money Move: hide `score40-v1` from non-admins; fix the 16px offset
- [ ] 3. News & Sentiment: 20-per-page pagination
- [ ] 4. Sidebar promo box: smaller, pinned, menu stays visible
- [ ] 5. Watchlist (already built at 78fd3852)
- [ ] 6. Cold-cache fallback so the index strip and sectors are never blank after a restart
- [ ] 7. The sweep's own defects
- [ ] 8. Build, hash, slot

## The live sweep, 2026-11-21

Driven through the SPA router from the owner's signed-in session. `role: user`, `isAdmin` false —
so everything below is what a NORMAL USER sees. Nothing was changed.

### Defects

| # | page | defect |
|---|------|--------|
| 1 | Analysis Intraday | **Vendor names visible**: "Unusual Whales, Finnhub, Finviz" in the statistics row. Owner: remove the whole row. |
| 2 | Analysis Intraday | Index strip 3 of 4 cards blank — the pulse priced **6 of 82 symbols** after the restart. Market Overview shows 5/5 from `/api/indices`, so the data path is fine; this is the pulse's cold start. |
| 3 | Smart Money Move | **`score40-v1` visible to non-admins** in the PageHead subtitle. Pre-dates kit N. |
| 4 | Smart Money Move | Live scanner table **overflows horizontally** (`right=1499` past the viewport). |
| 5 | Smart Money Move | Counts row and filters sit **16px** below the frame. |
| 6 | News & Sentiment | **All 40 headlines are dead links** — every `<a>` has no usable href. The Providers note already says a link appears only when the feed carries one; on this sample, none do. |
| 7 | News & Sentiment | **No pagination.** All 40 render; the frame paginates at 20 ("Showing 1–20 of 40", pages 1/2/›). This is the whole 836px height overflow against the frame. |
| 8 | Watchlist | `/watchlist` still renders the CLASSIC board inside the new shell, and its table overflows (`right=1175`). Kit N never contained the new page — it is in 78fd3852. |
| 9 | Options Flow | The tape table `tbl.mf-tape` overflows (`right=1658`). |
| 10 | Sidebar | The promo box is 213px tall and pushes menu items out of view. |

### Checked and clean

- No factor codes (`F3:`, `F7:`) on any page as a non-admin.
- Models render as Agent 1–4; no vendor name in Strategy Trades, News, Market Overview,
  Earnings Calendar, Post-Earnings AI, Overview or Options Flow.
- No dead links anywhere except News.
- Earnings Calendar, Post-Earnings AI and Overview (`/dashboard`) render with the shell's styling
  and no overflow.

### Two things I got wrong while sweeping, recorded so they are not repeated

- **`/overview` returns "Page not found" — and that is not a defect.** It is not a route; the
  menu's "Overview" points at `/dashboard`, which is healthy. I invented the path while probing.
- My first contrast probe flagged almost every element as white-on-white. It was wrong: the dark
  theme paints with gradients, so `background-color` is transparent and walking up the tree falls
  through to the document's white. The check was dropped rather than reported.
