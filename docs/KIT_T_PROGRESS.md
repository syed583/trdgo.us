# Kit T - Strategy Trades live event strip - progress

## Step 0 - Takion work protected (done)
- `takion/` committed on `codex/takion-phase1` as `b40b29cf` (15 source/doc files).
- NOT committed, by `takion/.gitignore`: local_secrets.json, local_certificate.pfx, sdk/, bin/, obj/, state/.
- `takion/` added to the LOCAL `.git/info/exclude`, so those leftovers cannot be staged from another branch.
- Kit T is built from `release/kit-n`. No takion/ file is in it.

## Step 1 - build (done)
- `frontend/src/components/StrategyEventStrip.tsx`: `diffTradeEvents`, `mergeEvents`, the strip.
- `NewStrategyTradesPage.tsx`: one diff now feeds the strip, the row flash + badge, and the voice.
- `new-strategy.css`: strip, blink, row flash, reduced-motion fallback.
- Events: BUY, SELL, STOP LOSS, TARGET, SCORE EXIT, plus CLOSED for end-of-day/bell/timed closes
  (real rows; dropping them or calling them a stop would both be wrong).
- Exit time: the payload has an exit DATE but no exit time of day, so an exit shows the ET time the
  page saw the change, marked "seen". Entries show the server's entry_time.

## Step 2 - tests (in progress)
- 17 new UI tests; suite 84/84. tsc (tsconfig.app.json) clean.
- Production build clean.
- `deploy/make_t1.py` written FRESH (not derived by text surgery): masks `.ps1` (it contains `s1`),
  checks stray letter pairs in both cases, refuses while the pins are placeholders.
- Browser simulation, first attempt: INVALID. It switched the data on "the second call", but the
  top-bar bell reads /api/strategy-trades too, so the page's FIRST read already carried the changes
  and (correctly) seeded them silently. Re-run with a flag flipped after the baseline is on screen.

## Step 2 - verified and packaged (done)
- Browser, hard loads, dark+light x 1536+1024, one simulated event of each type:
  5 events blinking + 3 rows flashing on arrival; 0 blinking and 5 still shown after 6 s;
  no re-blink on the next refresh; page overflow 0 px; strip above the table.
- Found by the 1024 px shot and fixed: the filter row was pinned (`height:58px; nowrap;
  overflow:hidden`), clipping the voice-alert button on top of the Symbol box.
- Backend selection: one intermittent failure in tests/test_unusualwhales.py on one run; passes
  alone and on re-run. Kit T changes no backend file.
- Source commit `c27b5b74`. Package commit `c1601f538c9953ee3f3bb7f4154763c380f39fb8`,
  anchor `6362f0304ccc6e8b4063a40192e693cb461cb9ab20b5e889bf5ba4790b69da09`.
- Runner `deploy/t1_run.ps1`, SHA-256
  `88CE2CCF0F323A2C9EBA79C54F60A6A93BF286C339F4879269707D575956E912`, installer commit `8aaaa5a7`.
- PreCheck must read `backend files: 0 changed` (frontend only).
- NOT pushed, NOT installed.
