# Unusual Whales token isolation plan (prepared 2026-10-24; nothing applied)

> **DECISION 2026-10-26: no second subscription.** Unusual Whales support confirmed one token per subscription (a second
> token would need a separate paid account). `us-signals-platform` has been stopped and stays stopped; TRDGO-US is the
> only consumer of the token (VPS audit of 2026-10-26: no other env file, process, scheduled task or container holds it;
> the only other copies are TRDGO-US's own `.env.bak` and `.env.seoul_*` backups). The sections below are kept as the plan
> to follow IF a second token is ever bought; today the protection is the built-in three-request cap, priority gate,
> per-machine slots and backoff.

Goal: TRDGO-US and us-signals-platform each hold their own token, so each has its own three-connection
allowance - **only after Unusual Whales confirms that arrangement in writing.** No credential appears in this
document; tokens are compared by their last four characters only.

## 0. Questions the provider must answer first (send to support)

1. Can this account issue more than one API token? At what price?
2. Does every token have its OWN three-simultaneous-request allowance, or is concurrency counted per account / per IP?
3. Do all tokens carry the same entitlements (the Whale Bundle Advanced data, websockets)?
4. We measured 6 parallel workers on 2026-10-21 (market hours) with no 429 and 8 workers with 429s, while support says
   to design to 3. Is the real threshold load-dependent? What number should we plan on?
5. How is a token revoked / rotated without downtime?

## 1. Where each token lives (names only)

| Application | Setting | File / place |
|---|---|---|
| TRDGO-US production (VPS) | `UNUSUAL_WHALES_API_KEY` (or the admin "provider key" override) | server `.env`; read the masked fingerprint at `/api/admin/provider/key` |
| TRDGO-US development | `UNUSUAL_WHALES_API_KEY` | `backend/.env` |
| us-signals-platform (backend, recorder, engine, scripts) | `UNUSUAL_WHALES_API_KEY` | its `.env` (read through its settings class) |

## 2. Assignment

| Consumer | Token | Connections |
|---|---|---|
| TRDGO-US production | A | `UW_MAX_INFLIGHT=3` |
| us-signals-platform (everything) | B | its own clients; recorder default is 4 workers = over its own 3 |
| TRDGO-US development machine | C if the provider issues one; otherwise B at 1 connection, or no key | `UW_MAX_INFLIGHT=1` (default off production) |

If only two tokens exist, the development machine must not call the provider during 04:00-20:00 ET.

## 3. Cut-over procedure (after the provider confirms; each step reversible)

1. Create token A (or B) in the provider dashboard. Do not paste it in chat, tickets or git.
2. Record the last four characters of every token (A, B, current) in a private note.
3. Keep the current token in place as the fallback until the soak below passes.
4. Put the new token into ONE application's `.env` only (the application that moves). File permissions: readable by the service account only.
5. Restart that application in a quiet window (outside 04:00-20:00 ET for the recorder; outside market hours for TRDGO-US). Do not restart both in one window.
6. Verify identity without exposing anything:
   * TRDGO-US: `/api/admin/provider/key` fingerprint ends with the NEW token's last four characters, and differs from us-signals-platform's;
   * us-signals-platform: compare its `.env` value to the other application's by equality only (script prints True/False).
7. Soak one full US session. Success: `/api/uw/health` OK, concurrency 429s 0 to a handful, the provider's 24-hour 429 count falls, and the other application's own `rate_limited` counter stays low.
8. Only then retire the old token from the application that no longer needs it (revoke in the dashboard).
9. Optional, with approval: a two-minute independence test: 3 simultaneous requests on token A while 3 run on token B; both must see zero concurrency 429s.

## 4. Rollback of the isolation

Put the previous token back into that application's `.env` and restart it. Because the old token was kept until step 8, nothing is lost. If step 8 already happened, issue a replacement from the dashboard.

## 5. What does NOT change

TRDGO-US trading logic, the 5-minute analysis and the mandatory 15-minute confirmation are independent of tokens. Neither application's production is touched by preparing this plan.
