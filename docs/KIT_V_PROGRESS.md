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
- Pushed and INSTALLED by the owner 2026-11-26 15:43 VPS. PreCheck read `4 changed, 1 new, 219 identical`;
  Update and PostCheck passed (/health 200, release_check PASS).
  Backup: `C:\apps\us-stock-reader\_backup_v1_20261126_154315` (344 files).
- Still to do after install: gateway check (`mt5_gateway_check.py --login 250094`), phantom list against the
  broker, reconcile dry run, then apply only with the owner's approval. Not yet confirmed that the two
  `MT5_GATEWAY_*` lines are in `backend\.env`.

## Kit W - follow-up found by the first real gateway call (2026-11-26)
- After the kit V install the paid host accepted the key (no key = 401 `No 'ApiKey' provided`; our call got past
  that) but refused ConnectEx: `Id parameter is required`. Its API description calls `id` optional; the host does not.
- Fix: `mt5_bridge.connect()` sends a fresh `uuid4` session id when `MT5_GATEWAY_URL` is set. One file. Two new tests.
- `mt5_phantom_list.py --broker` still names `HistoryDealsByPositionId` in its banner; cosmetic, script not changed.
- Pins: package `407a1c2497cc7b0b66eb43bcd12f90cba5c36856`, anchor
  `ee6e85c5208fec84a5e91b7688cefaac82691e45360ea642110b139245614150`, runner `deploy/w1_run.ps1` SHA-256
  `C80516D112941505EA5685BE0CA47B7DA5C3F1DB0E91AEB50FEDC73C24F1C3A0`, installer commit `79e6b3c5`.
  PreCheck must read `backend files: 1 changed, 0 new`. NOT pushed, NOT installed.
- Not proven: that a caller-chosen id is all the paid host wants. First proof is the gateway check after install.
