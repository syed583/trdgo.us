# Provider licensing and API-limits register (2026-11-04)

Status words: **VERIFIED** (confirmed from the owner's plan statement, the provider's spec file in the repo, or code that enforces it), **UNKNOWN** (nothing in the repo or from the owner establishes it), **REQUIRES OWNER CONFIRMATION** (the owner has reported it or the code assumes it, but it is not independently verified in writing).
No price, quota or licence term below was invented. No secret appears here; keys are named by environment variable only.

| Provider | Used for | Env var | Request limit | Concurrency | Cost | Licence for storage / research / multi-machine |
|---|---|---|---|---|---|---|
| Unusual Whales | Options flow, earnings calendar, estimates, news, chains, Smart Money Move evidence | `UNUSUAL_WHALES_API_KEY` (monitor/collector use the same token) | No daily or per-minute cap: owner-confirmed unlimited plan, REQUIRES OWNER CONFIRMATION in writing; usage headers in the repo's API spec agree | 3 simultaneous REST requests per token across every machine: VERIFIED (from response headers; enforced in code by the lease broker and the runner's sequential mode) | UNKNOWN (set `PROVIDER_PLAN_USD_MONTH`) | Public terms: personal use, no redistribution. Storing results for research, running on a second machine and WebSocket use: UNKNOWN; owner reports permission was granted (2026-11-02): REQUIRES OWNER CONFIRMATION |
| Finnhub | Calendar cross-check, dispersion for the Grok/Tradgo models | `FINNHUB_API_KEY` | UNKNOWN. The application paces itself at `FINNHUB_PER_MINUTE` (default 55): a self-imposed setting, not a verified quota | UNKNOWN | UNKNOWN | UNKNOWN |
| FINVIZ Elite | Calendar cross-check, screener data | `FINVIZ_API_TOKEN` | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| SEC EDGAR | Filings, 13F, 8-K | none | SEC fair-access guidance publishes a ceiling, not re-verified here: UNKNOWN for this deployment | not set | Free | Public data; the SEC asks for a descriptive User-Agent: REQUIRES OWNER CONFIRMATION that one is set |
| State Street SPDR | Daily ETF holdings | none | UNKNOWN | UNKNOWN | Free | UNKNOWN |
| Nasdaq dividends | Dividend dates | none | UNKNOWN (undocumented endpoint) | UNKNOWN | Free | UNKNOWN; undocumented endpoints can change or be withdrawn |
| Anthropic (Claude) | On-demand analysis buttons | `ANTHROPIC_API_KEY` (+ workspace id) | Set by the account | not set | Per request, only on a button press | Governed by Anthropic's terms for the account |
| WhatsApp Cloud API | Phone OTP login | `WHATSAPP_API_TOKEN` | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |

## How limits are made configurable and tracked
* `PROVIDER_LIMITS` (JSON, e.g. `{"FINNHUB": {"per_minute": 60, "per_day": null, "concurrent": 3}}`) supplies any figure the owner confirms; the ledger shows its source and computes "remaining today" only when a daily figure is configured.
* `PROVIDER_PLAN_USD_MONTH` (JSON) supplies a monthly price; the effective price per 1,000 requests is computed only then.
* Every request through Finnhub, Finviz and Unusual Whales is counted in the provider ledger (`/api/admin/provider-ledger`, and the Health tab of the monitor once the A.5 application component is deployed). Unusual Whales totals are reconciled across process restarts without double counting; the total can lag the truth by up to one flush interval and is never ahead of it.
* Retry handling: Unusual Whales client and the Smart Money Move governor stop on a 429 until midnight New York by default; an opt-in policy (`SMART_MOVE_429_POLICY=backoff`) uses Retry-After, bounded jittered backoff and a circuit breaker. Finnhub and Finviz have pacing only; their retry behaviour is whatever each service module does and is not provider-verified.

## Owner confirmations needed before any provider-facing expansion
1. Written confirmation that Unusual Whales permits storing results for research, running on more than one machine, and the intended request volume.
2. Plan quotas and prices for Finnhub and FINVIZ Elite (or confirmation that none apply).
3. The WhatsApp Cloud API plan, if OTP volume matters.
4. A descriptive SEC User-Agent contact string.

## Public-documentation check (2026-11-04, read-only page fetches; no provider API was called)
| Provider | What the public page states | Status |
|---|---|---|
| Unusual Whales (api.unusualwhales.com/docs) | Rate limits, concurrency, licence, redistribution and storage terms are **not stated** on the page. It links a usage guide ("Check API Usage") but gives no quota figures. It says redistribution licences are available on request for Enterprise/Professional subscribers. Published price seen: historical option trades $250/month full market (an add-on, not necessarily this account's plan). | Limits and terms UNKNOWN from public text. The 3-concurrent and no-daily-cap figures come from the owner's plan and response headers (see table above). Storage/commercial-use/redistribution: REQUIRES OWNER CONFIRMATION |
| Finviz Elite (finviz.com/elite.ashx) | List price $39.50/month or $299.50/year (page also shows $24.96/month billed annually). Export/API rate limits, data-use and redistribution terms are **not stated**. Quotes delayed 1 minute (free tier footer). | List price VERIFIED as published; this account's actual plan UNKNOWN; limits and terms UNKNOWN; REQUIRES OWNER CONFIRMATION |
| SEC EDGAR (sec.gov accessing-edgar-data) | Maximum request rate 10 requests per second; requesters must identify themselves in the User-Agent (company name plus contact email). | VERIFIED (public page). Whether this deployment's User-Agent is set: REQUIRES OWNER CONFIRMATION |
| Finnhub (finnhub.io/docs/api/rate-limit) | The page text could not be retrieved in usable form: no figures obtained. | UNKNOWN; do not rely on the 55-per-minute pacing as a provider limit |

Commercial-use restriction: nothing above grants commercial or redistribution rights. Treat all provider data as private to the owner's own use until written terms say otherwise.

Known costs: Finviz list price above; every other cost is UNKNOWN. Nothing in the application assumes a price.
