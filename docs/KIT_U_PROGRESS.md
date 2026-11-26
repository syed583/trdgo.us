# Kit U - owner UI changes of 2026-11-26 - progress

Built from `release/kit-n`. No takion/ file is in it.

1. Landing hero: the four link cards removed (`NewAnalysisLanding.tsx`). Pages stay in the side menu.
2. Smart Money filter pills (`new-figma.css`): one line per label, row wraps. Measured with the owner's
   twelve live labels placed in the real filter row (the local fixture has no Event-universe rows):
   6 pills spilling -> 0 at 1536 / 1280 / 1024; page overflow 160 px and 416 px -> 0.
3. IV Crush "no prices" (`tab_b_service.py`, `tab_settings.py`): live data read from the owner's
   logged-in page showed 57 of 64 rows computed EMPTY on 20-21 Nov and never recomputed (scan only queued
   rows that were missing altogether). Empty rows retry after 30 min, priced rows refresh after 6 h.
   Four new tests; the two IV Crush test files pass (26). WHY the rows were empty that day: not known.
4. Post-Earnings AI hidden from the menu (scheduler last ran 2026-10-22; 5 simulated trades).
5. Institutional Intelligence hidden from the menu (live API: "dev dataset is not configured").
   Both routes and their data are kept.
6. My Broker redesign (`MyBrokerPage.tsx`, `my-broker.css`): hero, tiles, cards, buttons, tables.
   Presentation only - no handler, confirmation, field name or request changed. Checked with mocked
   GET payloads (no POST sent) in connected / paused / not-connected states, both themes,
   1536 / 1024 / 390: nothing clipped, page overflow 0.

UI suite 84/84, tsc (tsconfig.app.json) clean, production build clean.

- Package commit `5a9a16c8e7a429e96801793b3de32d31c491ff2a`,
  anchor `df9089b1c5717ad8c20c69f05333129984e2e485ba4ce9f96a7e42146514d927`.
- Runner `deploy/u1_run.ps1`, SHA-256
  `3F944A97473B55527F7F96FFE60D5860C701D6114D39D0E4F6F2B9EE67681217` (derived from the kit T runner by
  `deploy/make_u1.py`; differs from it only in the header, the letter, and the two pins).
- PreCheck must read `backend files: 2 changed` (tab_b_service.py, tab_settings.py).
- NOT pushed, NOT installed.
