# US-Stock Reader

*Trade smarter. Faster.*

A personal US-equity research application: a directional model scored
parameter by parameter, live options flow, and the filings behind a company --
with every number traceable to the provider that published it.

Single-operator tool. **It never places orders** -- there is no brokerage
connection in this codebase at all. It reads market data over HTTP and has
nothing to place an order with.

## Where the data comes from

| Source | What it powers |
|---|---|
| **Unusual Whales** | The options tape and its unusual filter, market / stock / chain flow, dark-pool prints, bars and quotes, earnings and the expected move priced into each report, dividends, analyst actions, news, insider transactions, the screener, fundamentals and short interest |
| **SEC EDGAR** | 8-K item codes -- the company's own classification of what happened -- and the 13F census of every filer |
| **Claude** | The plain-English explanations. It is given the app's own figures and is never allowed to supply one |

Interactive Brokers, Finviz, Benzinga, Twelve Data, Alpha Vantage, Marketaux
and Yahoo were all removed. The free feeds each sat behind Unusual Whales as
a fallback, and a fallback that only fires when the paid feed is down would
be filling one panel on a screen whose tape, chain and quotes had already
gone with it. Nasdaq dividends and the SPDR daily holdings stay: both answer
questions nothing else does.

## Running it on a server

`deploy/` has what you need:

* `deploy/windows/` -- Windows VPS: NSSM service, Caddy for HTTPS, update script
* `deploy/README.md` -- Linux VPS: systemd unit, nginx config, update script

Both bind the app to localhost and put a reverse proxy in front. Neither
should be run without `ACCESS_PASSWORD` set.

---

## What's in the box

```
backend/     FastAPI + SQLAlchemy services (Python 3.12+)
frontend/    React + TypeScript + Vite dashboard
saved_work/  project_progress_summary.md — current state, decisions, gaps
```

This archive contains source only. `node_modules/`, the Python virtualenvs,
`__pycache__/` and `dist/` are excluded and are restored by the setup below.
`backend/.env` is also excluded because it holds a database credential — copy
`backend/.env.example` and fill it in.

---

## Requirements

- Python 3.12 or newer
- Node 18 or newer
- PostgreSQL 16 (a Docker container is fine)

---

## Setup

```bash
# 1. database
docker run -d --name trdgo-postgres -p 5432:5432 \
  -e POSTGRES_USER=trdgo -e POSTGRES_PASSWORD=trdgo123 \
  -e POSTGRES_DB=trdgo_us postgres:16

# 2. backend
cd backend
cp .env.example .env            # then edit if your DB differs
pip install fastapi "uvicorn[standard]" sqlalchemy psycopg[binary] \
            python-dotenv pytest
python -c "from database import engine; import models, models_user, models_earnings; \
           models.Base.metadata.create_all(engine); \
           models_user.create_all(engine); models_earnings.create_all(engine)"
python -m uvicorn main:app --host 127.0.0.1 --port 8000

# 3. frontend (separate terminal)
cd frontend
npm install
npm run dev                     # http://127.0.0.1:5173
```

Open <http://127.0.0.1:5173> and check **Settings** — it shows the live status
of every provider.

---

## Which API is used where, and why

Six APIs remain. Each is here because it answers something the others
cannot; where two could answer, the one that is paid for and covers the
whole market wins, and the other was removed rather than left as a fallback.

| Screen | Feed | Why that one |
|---|---|---|
| Options tape, unusual filter, market / stock / chain flow, dark pool, market insiders | **Unusual Whales** | The only feed here that publishes per-trade options flow with the side and the premium -- counted by the provider, not inferred here from where a print landed in the spread. |
| Quotes, daily and intraday bars, charts, the Market Overview strip | **Unusual Whales** | One source, one shape. Bars older than today are tagged `delayed=True` rather than passed off as live. |
| Option chain: bid, ask, implied volatility, greeks, open interest | **Unusual Whales** | Published per contract in one request. These used to be solved here with Black-Scholes from the quoted mid, which was this app's arithmetic rather than a measurement. |
| Market scanner | **Unusual Whales** | Ranked across every optionable US name, with the quote, RSI and moving-average posture already on each row. |
| Ticker search and validation | **SEC** + **Unusual Whales** | The registry proves the issuer exists; the feed says whether there is any data to show for it. A ticker can pass one and fail the other. |
| Earnings calendar, the expected move, estimates, reported results, dividends | **Unusual Whales** | It prices the straddle into each report, which is the first thing anyone asks about a date they are holding into. The calendar it replaced had the date and the estimate and no way to say that. |
| Analyst actions and the consensus panel | **Unusual Whales** | Covers the market. The ratings feed it replaced covered a restricted universe, so "no analyst actions" read as "nobody covers this stock" when it meant "we do not carry it". |
| News, the news desk, headline tone | **Unusual Whales** | No daily article cap, and each headline carries the tickers it is about. The tone beside a headline is this app's own keyword estimate and every payload says so -- the provider sentiment model went with Marketaux. |
| Institutional holdings, 13F, insider transactions, 8-K events | **SEC EDGAR** + **Unusual Whales** | The filings are the primary record and are free; the feed adds the insider transactions and the ownership census on top. |
| Dividend calendar dates | **Nasdaq** | Free, official, and the only one here that publishes the full forward dividend calendar. |
| ETF daily holdings | **SPDR** | The issuer's own file. Nothing else publishes it daily. |
| Every plain-English explanation | **Claude** | It is handed the app's own figures and is never allowed to supply one. |

**Interactive Brokers was removed.** TWS was the live book and cost nothing
per call, but it is a desktop application that has to be logged in, and on
this install it had not been running for some time -- every cold page paid
about four seconds per call to attempt a connection, be refused, and fall
through to the feed that answered anyway. Roughly 3,000 lines of broker
plumbing went with it, including a legacy options chain that opened its own
sockets and which no screen had called in a long time.

**The free providers were removed too** -- Finviz, Benzinga, Twelve Data,
Alpha Vantage, Marketaux and Yahoo. Each sat behind Unusual Whales as a
fallback, and a fallback that only fires when the paid feed is down would be
filling one panel on a screen whose tape, chain and quotes had already gone
with it.

**Four things genuinely went with them**, and each is labelled on screen as
missing rather than quietly approximated:

- A true pre-market and after-hours print (IBKR). Outside regular hours the
  app shows the last completed session's close, said as such.
- IBKR's 1-year implied-volatility history, replaced by the provider's
  published IV rank -- a comparable measurement, not the same one.
- The 7/30/60/90-day estimate-snapshot comparison (Alpha Vantage).
- Per-article sentiment scoring (Marketaux).

Two things got **better**, not merely replaced: implied volatility and the
greeks are now published per contract rather than solved here from the
quoted mid, and each print's side is counted by the provider rather than
inferred from where it landed in the spread. Both were this app's own
reconstruction before.

Only `UNUSUAL_WHALES_API_KEY` and `ANTHROPIC_API_KEY` are needed. SEC,
Nasdaq and SPDR need no key. Anything that cannot be answered reports
`PROVIDER_NOT_CONFIGURED` or `NO_DATA`. Nothing is faked to fill the gap.

---

## The one rule this codebase enforces everywhere

**Unverified data never becomes a signal.**

Every payload carries a `status` and a `source`. Seed/test rows are tagged
`TEST_DATA` and are excluded from statistics *and* from the score — so a
fixture can never turn into a beat rate or a BUY. A high score with low
confidence stays a WATCH. Where a number cannot be obtained, the UI says which
provider would be required rather than showing a plausible-looking value.

---

## Tests

```bash
cd backend  && python -m pytest      # 167 tests
cd frontend && npm run build         # type-check + production build
```

---

## Safety

- No order placement. No auto-trading. No brokerage connection exists.
- `tests/test_no_trading.py` fails if a broker library is ever imported back.
- No endpoint returns an API key or credential.
- Confidence and risk gating cannot be bypassed from the UI.

See `saved_work/project_progress_summary.md` for current state, design
decisions and known gaps.
