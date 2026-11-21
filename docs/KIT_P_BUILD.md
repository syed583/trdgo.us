# Kit P - build, evidence, and install details (2026-11-21)

NOT INSTALLED. Built, verified and pushed only when the owner says so.

## Pins

| | |
|---|---|
| Commit (archive downloaded) | `ae2bb1668f2c2cd642fa864e8439caa1deec86fc` |
| Manifest anchor | `1278df70bb518efee9695975dff5090ea6d932d2fd2070f5dc09c84480acddb5` |
| Runner SHA-256 | `EBCDCCBAB6FC2148CA0270C297C00C0C6F4E18D5F72B6CC09E8629A1D93491D9` |
| Runner | `deploy/p1_run.ps1` (253 lines, CRLF, no BOM - same shape as kit O's) |
| Package | 221 backend files, 120 frontend files, 8 removals, 344 manifest entries |
| Confirm words | `YES-INSTALL-P1`, `YES-ROLLBACK-P1`, `YES-RECOVER-P1` |

The commit pinned is the one that CONTAINS the built package, not the source commit before it.
`deploy/make_p1.py` refuses to write the runner if a kit O identifier or either of kit O's pins
survives the letter bump.

## How the before/after evidence was produced

Two dev servers, **one fixture backend**:

* **BEFORE** - `:5312`, a second worktree checked out at `fb6c3df9` (kit O, what is installed).
* **AFTER** - `:5311`, this worktree with kit P applied.
* Both proxy `/api` to the SAME fixture on `:8000`.

So the only difference inside a pair is kit P itself. Every shot is a **hard load** (`goto`),
never a `pushState`. Script: `frontend/figma-demo/_kitp_pairs.mjs` (gitignored - figma-demo never
ships).

**The numbers in the shots are fixture values, not live market data.** The real backend cannot run
locally (it would reach the production DB, which is forbidden) and kit P is not on the server yet,
so no "after" against live data can exist until it is installed. The evidence that each DEFECT is
real came from the live read-only click test in `KIT_P_CLICK_TEST.md`; these pairs are the
evidence that each FIX works.

Two fixture values were set to the exact strings the live server returns, so the fix could be seen:
the Smart Move `note` ("Rows are the frozen score40-v1 engine's own output...") and a row whose
`reason` is "blocked: SUSPECT_WINDOW, INSUFFICIENT_COVERAGE". Both were captured live.

## The thirteen

| # | before | after | shot |
|---|--------|-------|------|
| 1 | clicking AAPL landed on `/ai-insights/NVDA?symbol=AAPL` and analysed **NVDA**, ticker box empty | `/ai-insights/AAPL`, "Searching AAPL…" | yes |
| 2 | 5 tiles at 1040px, text sliced ("1 : 0.72" -> "0.72", "-0.03%" clipped) | 3 columns, nothing clipped | yes |
| 3 | clicking the profile chip did **nothing** (inert `div`) | menu: identity, Help & assistant, Sign out (+ admin links for admins) | yes |
| 4 | headphones icon labelled "Help" -> `/settings` | gone; bell shows an unread dot | yes |
| 5 | bell jumped to `/alerts` (the rule builder) | dropdown: 20 trades, side/price/time, Sound on, footer links | yes |
| 6 | promo pinned in the rail, hiding 4 menu rows | inside the scrolling nav, last; Overview/Stocks/Options/OI Build-Up now visible | yes |
| 7 | "powered by Claude on your UW data" | "grounded on this account's own market data" | yes |
| 8 | SOXL, SPDN, TQQQ in a **stock** movers list | common stock + ADRs only, failing open | **no - see below** |
| 9 | "the frozen **score40-v1** engine's own output" | "the frozen engine's own output" | yes |
| 10 | Historical snapshot tab visible to a non-admin | gone for non-admins | yes |
| 11 | "blocked: SUSPECT_WINDOW, INSUFFICIENT_COVERAGE" | "blocked: the earnings window looks wrong, not enough of the evidence was collected" | yes |
| 12 | Bid `$0.00`, Ask `$0.00`, Spread `$0.00` | Bid —, Ask —, Spread —, Last still a real price | yes |
| 13 | every headline an `<a>` with no `href` | 10 linked, 10 plain text, 0 dead anchors | probe |

**Item 8 has no screenshot and cannot have one**: the browser check runs against the fixture
backend, which replaces `uw_screener_service` wholesale, so the filter is invisible there. Its
evidence is `backend/tests/test_movers_issue_type_filter.py` - three tests that PASS on kit P and
**FAIL on the pre-kit-P module** (verified by running them in the before worktree).

**Item 13's pair looks nearly identical** by design: the fix is the anchor wrapper, not the
appearance. The proof is the DOM probe - `anchors: 10, plain: 10, deadAnchors: 0`.

## Checks

* UI suite `npm run test:ui` - **57/57**
* Targeted backend tests - `test_new_analysis_landing_and_smart_move`, `test_new_news_page`, the
  screener/movers/universe selection, and the new item 8 file - **all pass**
* `tsc -p tsconfig.app.json` - clean (this is the config `npm run build` uses; `tsconfig.json` is
  looser and misses real errors)
* `npm run build` - clean
* `dist/` grep: no Figma sample values. `score40-v1` and the vendor names that remain are inside
  **admin-gated conditionals** (`${isAdmin ? ' (score40-v1)' : ''}`) - the B.1 pattern, unchanged.

## One thing that is NOT in kit P

`vite.config.ts` gained a build-time stub for `figma-demo/`. That directory is **untracked**, and
`src/DevFigmaPreview.tsx` dynamically imports from it - so without the stub `vite build` on the
server cannot resolve those imports at all. It also keeps the Figma sample values out of `dist/`
(a grep previously found NVDA at $184.72 in the shipped assets). It is in the kit because the kit
cannot build without it.

## Found while testing, NOT fixed here

The assistant panel's input box renders black-on-dark in the **light** theme - present in the
before build too, so it is pre-existing, not kit P. Left for a later kit rather than widening
scope.
