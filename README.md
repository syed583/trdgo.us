# US-Stock Reader

*Trade smarter. Faster.*

A personal US market-intelligence application: live Interactive Brokers market
data, earnings scoring, options analytics, scanning and a trade journal.

Single-operator tool. **It never places orders** — the IBKR connection is
opened `readonly=True` and no order-placement call exists in the codebase.

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
- Interactive Brokers **TWS** or **IB Gateway**, logged in, with
  *Configure → API → Enable ActiveX and Socket Clients* switched on

---

## Setup

```bash
# 1. database
docker run -d --name trdgo-postgres -p 5432:5432 \
  -e POSTGRES_USER=trdgo -e POSTGRES_PASSWORD=trdgo123 \
  -e POSTGRES_DB=trdgo_us postgres:16

# 2. backend
cd backend
cp .env.example .env            # then edit if your DB/TWS differ
pip install fastapi "uvicorn[standard]" sqlalchemy psycopg[binary] \
            python-dotenv ib-insync pytest
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

## Data sources

| Source | Provides | Needs |
|---|---|---|
| **IBKR** | Quotes, OHLCV, option chains, open interest, IV/greeks, news, market scanner | TWS running |
| **SEC EDGAR** | Fundamentals from official filings | nothing |
| **Benzinga** *(optional)* | Earnings calendar, actual results, history | `BENZINGA_API_KEY` |
| **Alpha Vantage** *(optional)* | Analyst estimates and revisions | `ALPHA_VANTAGE_API_KEY` |

Without the optional keys the app runs fine on IBKR + SEC and reports
`PROVIDER_NOT_CONFIGURED` for the features that need them. Nothing is faked to
fill the gap.

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

- No order placement. No auto-trading.
- IBKR connects read-only.
- No endpoint returns an API key or credential.
- Confidence and risk gating cannot be bypassed from the UI.

See `saved_work/project_progress_summary.md` for current state, design
decisions and known gaps.
