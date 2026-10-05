import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowUpRight, ArrowDownRight, RefreshCw, Info, ShieldAlert,
  Target, LogIn, Ban, Clock, Plus, X, Star,
} from 'lucide-react';
import type { PageContext } from '../App';
import { api, api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import { num } from '../lib/format';
import { PageHead } from './shared';
import { ScoreGauge } from '../components/common';
import SignalsPanel from '../components/SignalsPanel';
import './trade-plan.css';

const HORIZONS = ['TODAY', 'TOMORROW', 'SWING'];

const STATUS_LABEL: Record<string, string> = {
  PENDING: 'Waiting for entry',
  ACTIVE: 'In the entry zone',
  TP1_HIT: 'Target 1 hit',
  TP2_HIT: 'Target 2 hit',
  TP3_HIT: 'Target 3 hit',
  SL_HIT: 'Stopped out',
  EXPIRED: 'Expired untouched',
  INVALIDATED: 'Invalidated',
};
const STATUS_TONE: Record<string, string> = {
  PENDING: 'wait', ACTIVE: 'live', TP1_HIT: 'win', TP2_HIT: 'win', TP3_HIT: 'win',
  SL_HIT: 'loss', EXPIRED: 'flat', INVALIDATED: 'flat',
};

/** Plain-English conviction from the confidence number. */
function convictionLabel(c: number | null | undefined): string {
  if (c == null) return 'Unrated';
  if (c >= 80) return 'High conviction';
  if (c >= 65) return 'Solid conviction';
  if (c >= 50) return 'Moderate conviction';
  return 'Low conviction';
}

function Price({ v }: { v: number | null | undefined }) {
  return <span className="tp-price">{v == null ? '--' : num(v, 2)}</span>;
}

/**
 * Live top-of-book for the underlying, refreshed on its own short cycle so the
 * bid/ask floats in real time. Prefers the live quote; falls back to the plan's
 * snapshot until the quote lands. Outside regular hours it reads the extended
 * session's book when the regular one is empty.
 */
function QuoteStrip({ d, q, live }: { d: any; q: any; live: boolean }) {
  const ext = q?.extended || {};
  const bid = q?.bid ?? ext.bid ?? d.bid;
  const ask = q?.ask ?? ext.ask ?? d.ask;
  const last = q?.price ?? ext.price ?? d.spot;
  const chg = q?.change_percent ?? d.change_percent;
  const spread = (bid != null && ask != null) ? Math.round((ask - bid) * 100) / 100
    : d.spread;

  return (
    <div className="tp-quote">
      <div className="tp-q bid">
        <span className="tp-q-l">Bid</span>
        <span className="tp-q-v"><Price v={bid} /></span>
      </div>
      <div className="tp-q last">
        <span className="tp-q-l">
          Last {live && <span className="tp-live-dot" title="Live" />}
        </span>
        <span className="tp-q-v"><Price v={last} /></span>
        {chg != null && (
          <span className={`tp-q-chg ${chg >= 0 ? 'pos' : 'neg'}`}>
            {chg >= 0 ? '+' : ''}{num(chg, 2)}%
          </span>
        )}
      </div>
      <div className="tp-q ask">
        <span className="tp-q-l">Ask</span>
        <span className="tp-q-v"><Price v={ask} /></span>
      </div>
      <div className="tp-q spread">
        <span className="tp-q-l">Spread</span>
        <span className="tp-q-v">{spread != null ? num(spread, 2) : '--'}</span>
      </div>
    </div>
  );
}

/**
 * The one-line answer to "what do I do right now": buy/sell at a price, wait for
 * a pullback, wait because the location is poor, or stand aside. This is the
 * first thing the card says.
 */
function verdict(d: any): { tone: string; action: string; detail: string } {
  const long = d.bias === 'LONG';
  const verb = long ? 'Buy' : 'Sell';
  const side = long ? 'long' : 'short';
  const st = d.tracking?.status;
  const lo = num(d.entry?.low, 2), hi = num(d.entry?.high, 2);
  const spot = num(d.spot, 2);

  if (d.status === 'NO_SETUP')
    return { tone: 'flat', action: 'Stand aside',
      detail: 'No clear setup right now — the model does not point decisively enough.' };

  if (st === 'SL_HIT') return { tone: 'loss', action: 'Stopped out', detail: d.tracking.outcome_note };
  if (st === 'TP1_HIT' || st === 'TP2_HIT' || st === 'TP3_HIT')
    return { tone: 'win', action: 'Target reached', detail: d.tracking.outcome_note };
  if (st === 'EXPIRED') return { tone: 'flat', action: 'Expired', detail: d.tracking.outcome_note };
  if (st === 'INVALIDATED') return { tone: 'flat', action: 'Invalidated', detail: d.tracking.outcome_note };

  if (d.status === 'WEAK_SETUP')
    return { tone: 'wait', action: 'Wait — weak location',
      detail: `Reward:risk is ${num(d.reward_risk, 2)} (needs ${d.rr_min ?? 1.5}). `
        + `Better entries exist; no trade here.` };

  // A real, actionable setup.
  if (st === 'ACTIVE')
    return { tone: 'go', action: `${verb} now — in the zone`,
      detail: `Price ${spot} is inside the ${side} entry zone ${lo}–${hi}.` };

  // PENDING: waiting for price to reach the entry.
  const above = long ? (d.spot > d.entry?.high) : (d.spot < d.entry?.low);
  if (above)
    return { tone: 'wait', action: 'Wait for pullback',
      detail: `${verb} only in ${lo}–${hi}. Price now ${spot} is `
        + `${long ? 'above' : 'below'} the zone — do not chase.` };
  return { tone: 'go', action: `${verb} zone ${lo}–${hi}`,
    detail: `Price ${spot} is at the entry. ${d.do_not_chase_label} ${num(d.do_not_chase, 2)}.` };
}

/**
 * A horizontal track showing where price sits between the stop and the targets,
 * with the entry band shaded. Uses an ordinal (rank) scale -- every level gets
 * even spacing regardless of how far apart the prices are -- so a distant stop
 * no longer crams everything else into one corner. Labels show the real prices;
 * the reward:risk box carries the true distance ratio.
 */
function PriceLadder({ d }: { d: any }) {
  const long = d.bias === 'LONG';
  const PAD = 7;
  const EPS = 1e-6;

  const anchors = Array.from(
    new Set([d.stop, d.entry?.low, d.entry?.high, d.spot, d.targets?.tp1, d.targets?.tp2, d.targets?.tp3]
      .filter((x) => x != null) as number[]),
  ).sort((a, b) => a - b);
  if (anchors.length < 2) return null;
  const n = anchors.length;

  // Map a price to an evenly-spaced ordinal position (0..1), interpolating
  // linearly between the nearest anchors so the spot sits sensibly between them.
  const even = (v: number): number => {
    if (v <= anchors[0]) return 0;
    if (v >= anchors[n - 1]) return 1;
    let k = 0;
    while (k < n - 1 && !(v >= anchors[k] && v <= anchors[k + 1])) k++;
    const frac = (v - anchors[k]) / ((anchors[k + 1] - anchors[k]) || EPS);
    return (k + frac) / (n - 1);
  };
  const pos = (v: number) => PAD + even(v) * (100 - 2 * PAD);

  const el = pos(long ? d.entry.low : d.entry.high);
  const eh = pos(long ? d.entry.high : d.entry.low);

  const marks = [
    { v: d.stop, cls: 'stop', label: 'Stop' },
    { v: d.spot, cls: 'spot', label: 'Spot' },
    { v: d.targets.tp1, cls: 'tp', label: 'TP1' },
    { v: d.targets.tp2, cls: 'tp', label: 'TP2' },
    { v: d.targets.tp3, cls: 'tp', label: 'TP3' },
  ].filter((m) => m.v != null);

  return (
    <div className="tp-ladder">
      <div className={`tp-ladder-track ${long ? '' : 'rev'}`}>
        <div className="tp-ladder-zone"
          style={{ left: `${Math.min(el, eh)}%`,
            width: `${Math.max(Math.abs(eh - el), 4)}%` }} />
        {marks.map((m) => (
          <div key={m.label} className={`tp-ladder-mark ${m.cls}`}
            style={{ left: `${pos(m.v)}%` }}>
            <span className="tp-ladder-tick" />
            <span className="tp-ladder-cap">{m.label}<br />{num(m.v, 1)}</span>
          </div>
        ))}
      </div>
      <div className="tp-ladder-legend">
        <span>◀ {long ? 'risk (stop)' : 'reward (targets)'}</span>
        <span>{long ? 'reward (targets)' : 'risk (stop)'} ▶</span>
      </div>
    </div>
  );
}

export default function TradePlanPage({ ctx }: { ctx: PageContext }) {
  const { symbol, demo } = ctx;
  const navigate = useNavigate();
  const [horizon, setHorizon] = useState('SWING');
  const [addQ, setAddQ] = useState('');

  // Personal watchlist shown as a quick-switch bar at the top: pick a ticker to
  // load its plan, or add more. Every signed-in user manages their own.
  const wl = useApi<any>((s) => (demo ? Promise.resolve(null) : api2.watchlist(s)), [demo]);
  const wlRows: any[] = wl.data?.rows || [];
  const goSym = (s: string) => navigate(`/trade-plan/${s.toUpperCase()}${ctx.search}`);
  const addStock = async (e: React.FormEvent) => {
    e.preventDefault();
    const t = addQ.trim().toUpperCase();
    if (!t) return;
    setAddQ('');
    try { await api2.watchlistAdd(t); } catch { /* ignore */ }
    wl.refresh();
    goSym(t);
  };
  const removeStock = async (e: React.MouseEvent, s: string) => {
    e.stopPropagation();
    try { await api2.watchlistRemove(s); } catch { /* ignore */ }
    wl.refresh();
  };

  const plan = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.tradePlan(symbol, horizon, s)),
    [symbol, horizon, demo],
    { refreshMs: demo ? undefined : 30_000 },
  );
  const hist = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.tradePlanHistory(symbol, s)),
    [symbol, demo],
  );
  // The bid/ask float on their own fast cycle so the top-of-book is live, while
  // the plan's levels stay on their slower cache.
  const quote = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api.quote(symbol, s, 5)),
    [symbol, demo],
    { refreshMs: demo ? undefined : 6_000 },
  );
  // Model track record so the plan carries how reliable the signal has been:
  // this ticker's scorecard, falling back to the overall one for this horizon.
  const symCard = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.callScorecard(horizon, 120, symbol, s)),
    [symbol, horizon, demo],
  );
  const allCard = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.callScorecard(horizon, 120, undefined, s)),
    [horizon, demo],
  );
  const q = quote.data;
  const sym = symCard.data?.overall;
  const scope = (sym && sym.calls > 0) ? { d: symCard.data, label: symbol }
    : { d: allCard.data, label: 'all tracked' };
  const track = scope.d?.overall;
  const live = !!q && q.market?.is_open === true;

  const d = plan.data;
  const status = d?.status;
  const long = d?.bias === 'LONG';
  const t = d?.tracking;
  const conf = d?.confidence as number | null | undefined;
  const confColor = conf == null ? 'var(--text-mute)'
    : conf >= 65 ? 'var(--green)' : conf >= 50 ? 'var(--amber)' : 'var(--red)';

  const controls = (
    <>
      <div className="tp-horizons">
        {HORIZONS.map((h) => (
          <button key={h}
            className={`tp-h-btn ${horizon === h ? 'active' : ''}`}
            onClick={() => setHorizon(h)}>{h}</button>
        ))}
      </div>
      <button className="btn-icon" onClick={() => { plan.refresh(); hist.refresh(); }}
        aria-label="Refresh" title="Refresh">
        <RefreshCw size={15} className={plan.loading ? 'tp-spin' : ''} />
      </button>
    </>
  );

  return (
    <div className="page tp">
      <PageHead
        title={`Trade Plan — ${symbol}`}
        subtitle={<>Data-derived levels: where the setup is supported, where to
          stop chasing, where it's wrong, and where it's going — with the
          reward:risk those levels imply. <b>Analysis, not advice.</b></>}
        right={controls}
      />

      {!demo && (
        <div className="tp-watchlist">
          <span className="tp-wl-icon"><Star size={14} /> Watchlist</span>
          <div className="tp-wl-chips">
            {wlRows.map((r) => (
              <button key={r.symbol}
                className={`tp-wl-chip ${r.symbol === symbol ? 'active' : ''}`}
                onClick={() => goSym(r.symbol)} title={`Open ${r.symbol} plan`}>
                <span className="tp-wl-sym">{r.symbol}</span>
                {r.change_percent != null && (
                  <span className={`tp-wl-chg ${r.change_percent >= 0 ? 'pos' : 'neg'}`}>
                    {r.change_percent >= 0 ? '+' : ''}{num(r.change_percent, 2)}%
                  </span>
                )}
                <span className="tp-wl-x" onClick={(e) => removeStock(e, r.symbol)}
                  title={`Remove ${r.symbol}`}><X size={11} /></span>
              </button>
            ))}
            {!wlRows.length && !wl.loading && (
              <span className="tp-wl-empty">No stocks yet — add one →</span>
            )}
          </div>
          <form className="tp-wl-add" onSubmit={addStock}>
            <input value={addQ} onChange={(e) => setAddQ(e.target.value)}
              placeholder="Add ticker…" aria-label="Add ticker to watchlist" />
            <button type="submit" aria-label="Add" title="Add to watchlist">
              <Plus size={14} />
            </button>
          </form>
        </div>
      )}

      {demo ? (
        <div className="tp-empty">Trade Plan is disabled in demo mode.</div>
      ) : plan.initialLoading ? (
        <div className="tp-empty">Building the plan from live levels…</div>
      ) : !d ? (
        <div className="tp-empty">No plan available.</div>
      ) : status === 'NO_SETUP' ? (
        <div className="tp-card tp-nosetup">
          <div className="tp-bias neutral">NO SETUP · {d.lean}</div>
          <QuoteStrip d={d} q={q} live={live} />
          <p>The model doesn't point decisively enough to place a plan right now.</p>
          <ul>{(d.reasons || []).map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
          <div className="tp-ctx">
            Spot <Price v={d.spot} /> · score {num(d.score, 0)} · support{' '}
            <Price v={d.support} /> · resistance <Price v={d.resistance} />
          </div>
        </div>
      ) : (
        <>
          {t && (
            <div className={`tp-status ${STATUS_TONE[t.status] || 'wait'}`}>
              <span className="tp-status-dot" />
              <b>{STATUS_LABEL[t.status] || t.status}</b>
              {t.outcome_note && <span className="tp-status-note">{t.outcome_note}</span>}
              {!t.outcome_note && t.last_price != null && (
                <span className="tp-status-note">Last <Price v={t.last_price} /></span>
              )}
            </div>
          )}

          <div className="tp-card">
            {/* The one-line "what do I do now" answer. */}
            {(() => {
              const v = verdict(d);
              return (
                <div className={`tp-verdict ${v.tone}`}>
                  <div className="tp-verdict-action">{v.action}</div>
                  <div className="tp-verdict-detail">{v.detail}</div>
                </div>
              );
            })()}

            <QuoteStrip d={d} q={q} live={live} />

            {/* Hero: bias + conviction meter + reward:risk */}
            <div className="tp-hero">
              <div className={`tp-bias ${long ? 'long' : 'short'}`}>
                {long ? <ArrowUpRight size={20} /> : <ArrowDownRight size={20} />}
                {d.bias}
              </div>

              <div className="tp-conf">
                <div className="tp-conf-gauge">
                  <ScoreGauge value={conf ?? 0} color={confColor} size={96} />
                  <div className="tp-conf-num" style={{ color: confColor }}>
                    {num(conf, 0)}
                  </div>
                </div>
                <div className="tp-conf-meta">
                  <div className="tp-conf-label">{convictionLabel(conf)}</div>
                  <div className="tp-conf-sub">{d.decision} · score {num(d.score, 0)}</div>
                </div>
              </div>

              <div className={`tp-rrbox ${d.reward_risk >= 2 ? 'good'
                : status === 'WEAK_SETUP' ? 'weak' : ''}`}>
                <div className="tp-rrbox-v">{num(d.reward_risk, 2)}</div>
                <div className="tp-rrbox-l">Reward : Risk</div>
              </div>
            </div>

            {status === 'WEAK_SETUP' && (
              <div className="tp-weak">
                <Info size={14} /> {(d.reasons || [])[0]
                  || 'Reward:risk is below threshold — poor location, better to wait.'}
              </div>
            )}

            <PriceLadder d={d} />

            {/* Clear enter -> exit summary. */}
            <div className="tp-enterexit">
              <div className="tp-ee enter">
                <span className="tp-ee-l">Enter ({long ? 'Buy' : 'Sell'})</span>
                <span className="tp-ee-v">
                  <Price v={d.entry?.low} /> – <Price v={d.entry?.high} />
                </span>
              </div>
              <span className="tp-ee-arrow">→</span>
              <div className="tp-ee exit-win">
                <span className="tp-ee-l">Exit — profit (target)</span>
                <span className="tp-ee-v"><Price v={d.targets?.tp1} /></span>
              </div>
              <div className="tp-ee exit-loss">
                <span className="tp-ee-l">Exit — loss (stop)</span>
                <span className="tp-ee-v"><Price v={d.stop} /></span>
              </div>
              <div className="tp-ee exit-time">
                <span className="tp-ee-l">Exit — time</span>
                <span className="tp-ee-v">{d.validity?.label || '--'}</span>
              </div>
            </div>

            {/* Model track record: how this score has actually performed. */}
            {track && track.calls > 0 && (
              <div className="tp-model">
                <span className="tp-model-ic"><Target size={14} /></span>
                <div className="tp-model-stat">
                  <span className="tp-model-l">Model hit rate</span>
                  <span className={`tp-model-v ${(track.hit_rate ?? 0) >= 55 ? 'pos'
                    : (track.hit_rate ?? 0) <= 45 ? 'neg' : ''}`}>
                    {track.hit_rate != null ? `${num(track.hit_rate, 0)}%` : '--'}
                  </span>
                </div>
                <div className="tp-model-stat">
                  <span className="tp-model-l">Avg excess vs SPY</span>
                  <span className={`tp-model-v ${(track.avg_excess_pct ?? 0) >= 0 ? 'pos' : 'neg'}`}>
                    {track.avg_excess_pct != null
                      ? `${track.avg_excess_pct >= 0 ? '+' : ''}${num(track.avg_excess_pct, 2)}%` : '--'}
                  </span>
                </div>
                <div className="tp-model-stat">
                  <span className="tp-model-l">Sample</span>
                  <span className="tp-model-v">{track.calls} {scope.label} · {horizon}</span>
                </div>
                {!scope.d?.enough_data && (
                  <span className="tp-model-note">limited sample</span>
                )}
              </div>
            )}

            <div className="tp-levels">
              <div className="tp-lv entry">
                <div className="tp-lv-ic"><LogIn size={16} /></div>
                <div>
                  <div className="tp-lv-l">Entry zone</div>
                  <div className="tp-lv-v"><Price v={d.entry.low} /> – <Price v={d.entry.high} /></div>
                </div>
              </div>
              <div className="tp-lv chase">
                <div className="tp-lv-ic"><Ban size={16} /></div>
                <div>
                  <div className="tp-lv-l">{d.do_not_chase_label}</div>
                  <div className="tp-lv-v"><Price v={d.do_not_chase} /></div>
                </div>
              </div>
              <div className="tp-lv stop">
                <div className="tp-lv-ic"><ShieldAlert size={16} /></div>
                <div>
                  <div className="tp-lv-l">Stop loss</div>
                  <div className="tp-lv-v"><Price v={d.stop} /></div>
                </div>
              </div>
              <div className="tp-lv tp1">
                <div className="tp-lv-ic"><Target size={16} /></div>
                <div>
                  <div className="tp-lv-l">Target 1</div>
                  <div className="tp-lv-v"><Price v={d.targets.tp1} /></div>
                </div>
              </div>
              <div className="tp-lv tp2">
                <div className="tp-lv-ic"><Target size={16} /></div>
                <div>
                  <div className="tp-lv-l">Target 2</div>
                  <div className="tp-lv-v"><Price v={d.targets.tp2} /></div>
                </div>
              </div>
              <div className="tp-lv tp3">
                <div className="tp-lv-ic"><Target size={16} /></div>
                <div>
                  <div className="tp-lv-l">Target 3</div>
                  <div className="tp-lv-v"><Price v={d.targets.tp3} /></div>
                </div>
              </div>
              <div className="tp-lv spot">
                <div className="tp-lv-ic" />
                <div>
                  <div className="tp-lv-l">Spot now</div>
                  <div className="tp-lv-v"><Price v={d.spot} /></div>
                </div>
              </div>
            </div>

            <div className="tp-validity">
              <Clock size={13} />
              {d.style && <span className="tp-style">{d.style}</span>}
              Valid: {d.validity?.label}{' · '}ATR {num(d.atr, 2)}
              {d.atr_basis ? ` (${d.atr_basis})` : ''}
              {d.issued_spot != null && (
                <span className="tp-fixed">
                  Levels fixed — issued @ {num(d.issued_spot, 2)}
                </span>
              )}
            </div>

            {d.read && <div className="tp-read">{d.read}</div>}
            <div className="tp-disclaimer">{d.disclaimer}</div>
          </div>
        </>
      )}

      {!demo && hist.data?.plans?.length > 1 && (
        <div className="tp-history">
          <h2>Past plans for {symbol}</h2>
          <table>
            <thead>
              <tr>
                <th>Issued</th><th>Bias</th><th className="r">Entry</th>
                <th className="r">Stop</th><th className="r">TP1</th><th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {hist.data.plans.slice(0, 20).map((p: any) => (
                <tr key={p.id}>
                  <td>{(p.issued_at || '').slice(0, 10)}</td>
                  <td className={p.bias === 'LONG' ? 'pos' : 'neg'}>{p.bias}</td>
                  <td className="r">{num(p.entry?.low, 1)}–{num(p.entry?.high, 1)}</td>
                  <td className="r">{num(p.stop, 1)}</td>
                  <td className="r">{num(p.targets?.tp1, 1)}</td>
                  <td>
                    <span className={`tp-chip ${STATUS_TONE[p.status] || 'flat'}`}>
                      {STATUS_LABEL[p.status] || p.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Signals + watchlist live right here in the Trade Plan. */}
      <SignalsPanel demo={demo} search={ctx.search} />
    </div>
  );
}
