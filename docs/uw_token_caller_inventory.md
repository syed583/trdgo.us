# Unusual Whales token: who calls it, and how

Audited read-only on 2026-10-24. No credentials are shown; tokens were compared by their last four characters
and by equality only. **The provider allows THREE simultaneous in-flight REST requests per token, across all
machines and applications together** (more -> 429 `concurrent_limit_exceeded`); there is no daily or per-minute cap.

## Token usage

| Application / place | Token | Evidence |
|---|---|---|
| US-STOCKS, this (dev) machine, `backend/.env` | ends `82b4` | key fingerprint |
| US-STOCKS, production VPS | **UNKNOWN** | read `/api/admin/provider/key` -> `fingerprint` on the server |
| us-signals-platform, `.env` (this machine) | ends `82b4` - **the same token value** | equality check |
| khalli-balli-risk | no Unusual Whales reference | file search |

## US-STOCKS / TRDGO-US callers (all share one gate of 3 + machine-wide lock files unless noted)

| Caller | Priority / module | In flight | Cadence | Bypasses the controls? |
|---|---|---|---|---|
| Live 5-minute engine (`intraday_signal_v2.scan_once`, `intraday_model`) | 1 `intraday_engine` | 1 (sequential) | tiers: 3 / 5 / 15-30 min per name | no |
| Shared feed for the six boards (`intraday_feed.start_background`) | 2 `ai_boards` | 1 (up to 3 inside one options overview) | 60 s tick, up to 6 names per tick, each name ~every 7 min | no |
| 15-minute candles (`intraday_15m.cycle`, in the feed loop) | 2 `candles_15m` | 1 | each closed 15-minute candle | no |
| Paper-trade tracker loop (`main.py`) | 2 `paper_trade_tracker` | 1 | loop | no |
| Page requests (API routes) | 3 (2 for quote / flow / chain paths) | fan-out, bounded by the gate | on demand | no |
| `market_pulse`, `trade_plan_scanner` loops | 3 | 1 | loop | no |
| Historical warmer (`local_warmer`) | 4 `warmer` | up to 3 threads, bounded to 1 slot in session | batches | no |
| earnings capture, snapshots, calendar sync, call scorecard loops | 4 | 1 each | scheduled | no |
| Slow-screen warmer at boot (`main.py`) | unlabelled -> 3 | 3 threads, bounded by the gate | once at start | no |
| `backend/data_health_check.py` (manual script) | none | 1 | when run | **was direct HTTP; now takes a machine-wide slot** |
| Development server on a non-production machine | any | **1 by default** (`UW_MAX_INFLIGHT`) | on demand | no |

## us-signals-platform callers (separate application; NOT under our controls)

| Caller | In flight | Cadence | Controls |
|---|---|---|---|
| `Prod-Signals-3-recorder` (`scripts/record.py` via `prod_supervisor`) | **4 workers by default** (one client each) | every 60 s cycle, **04:00-20:00 ET on trading days**; measured 384 requests / cycle, ~7.2 req/s while fetching, ~0.37 x 429 per cycle | own per-client `min_interval`; own retry on 429; knows "other processes share the keys" (cap 8 req/s); no shared limit |
| `Prod-Signals-4-engine` (`scripts/run_live.py`, standard loop) | 1 (sequential client) | every 60 s, up to 30 symbols x 3 requests + benchmark | own throttle 0.25 s |
| `Prod-Signals-1-backend` (`app/api.py`) | 1 per diagnostic request (a new client each time) | on demand | none |
| `scripts/measure_feed.py` | N parallel workers, no retries, no spacing | when run (stress test) | **none - deliberately saturates** |
| `scripts/research_build.py`, `replay_session.py`, `train_ai.py`, `audit_providers.py`, `latency_*`, shadow scripts | 1 each | when run | own throttle only |

## Root cause of the shared-token 429s

Recorder (up to 4 in flight) + engine (1) + our production client (formerly up to 8, now 3) + occasional scripts
= far more than 3 at once, for most of the US session. Nothing coordinates the two applications.
