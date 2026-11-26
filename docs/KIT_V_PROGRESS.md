# Kit V - MT5 gateway and mirror fix - progress

Built from `release/kit-n` on top of kit U. No takion/ file is in it. Kit U is unchanged.

## What is in it
- A. Gateway: `MT5_GATEWAY_URL` + `MT5_GATEWAY_KEY` from `backend\.env`; key sent as the `ApiKey` header to that
  host only; scrubbed from every result; alert + banner on licence or gateway failure.
  Read from the paid host's own API description: same 107 paths as the trial host, identical definitions;
  the ONLY difference is a global security scheme `apiKey`, name `ApiKey`, in `header`.
- B. Phantom positions: a close that finds the ticket gone is verified (position list + OrderHistory) and
  recorded CLOSED; `reconcile_user()`; sweep reconciles first; 2-minute grace for fresh fills; one delayed retry.
- C. Cap counts really-open positions; margin check (free margin vs RequiredMargin); limits 1-500 positions,
  daily loss 0 (off) to 20%; presets on My Broker.
- E. One worker per user (exits first), single-flight closes, per-step timing on each order row
  (new nullable column `timing` on `user_mt5_orders`, added by the service on first write).
- Every missed entry on a connected account is logged with a reason; reasons shown in plain words, no
  supplier name.

## What is NOT in it
- Part D, the per-user execution report (per-trade table, day/week summary, date filter, CSV, the
  cap/margin/not-mapped banners). Not built. The data it needs is now being recorded.
- The operator's own engine still calls a history endpoint that does not exist on this gateway
  (`HistoryDealsByPositionId` in `mt5_bridge.position_exit_deals`). Not touched: it is the operator's
  engine. Reported, not fixed.
- WHY 12 closes were over 60 s late is not proven. The structural cause (one worker for all users, three
  sequential gateway calls) is removed and each step is now timed, so the next late close will say why.

## Tests
279 backend tests across the MT5, copy-trading, gateway, plain-reason, stylesheet and IV Crush files pass
(7 gateway, 21 mirror, 3 plain-reason are new). 70 privacy-route tests pass. UI suite 84/84, tsc clean,
production build clean. My Broker checked in a browser in three states, both themes, 1536/1024/390:
nothing clipped, no overflow, no supplier name on the page.
NOT tested against the real gateway: nothing here has made a call to the paid host with the key.

## A mistake of mine found on the way
Kit U's My Broker stylesheet used three literal colours, which `test_f1a_my_broker_theme.py` forbids. I
had not run that test before shipping kit U. Fixed here (theme tokens); kit U on the server has the
literal colours until kit V is installed. Visual effect: none known.

## Pins
- Package commit `394db67932c1862989832b88c53cae9c21cbaa49`
- Anchor `93af239ff3b9aaf11249b820c2546d0a68fd6109875464bb4c2d76bb80ef8d47`
- Runner `deploy/v1_run.ps1`, SHA-256 `42AB693B7F85DC617CE7D06785C3B4EF85296B1594ECD901FC277677B36BA060`
- PreCheck must read `backend files: 4 changed, 1 new`.
- NOT pushed, NOT installed.
