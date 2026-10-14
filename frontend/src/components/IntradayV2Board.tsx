import { useState } from 'react';
import { Loader2, TrendingUp, TrendingDown, ShieldAlert, Minus } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './intraday-v2.css';

// Intraday Model v2 -- the Trdgo Stock TODAY board. Rendered only when the backend
// flag INTRADAY_MODEL=v2 is on (endpoint returns DISABLED otherwise -> the parent
// keeps the existing v1 board). Top Buyers / Sellers / NO EDGE / WITHHELD, the
// persisted signal log, and the daily win-rate summary.

const money = (v: any) => (v == null ? '—' : `$${Number(v).toFixed(2)}`);
const pct = (v: any) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(2)}%`);
const callClass = (c: string) =>
  c === 'STRONG BUY' || c === 'BUY' ? 'im2-buy'
    : c === 'STRONG SELL' || c === 'SELL' ? 'im2-sell' : 'im2-noedge';
const t = (iso: any) => {
  if (!iso) return '—';
  try {
    return new Intl.DateTimeFormat(undefined,
      { hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York' }).format(new Date(iso));
  } catch { return '—'; }
};

function SignalTable({ rows, side }: { rows: any[]; side: 'BUY' | 'SELL' }) {
  const nav = useNavigate();
  if (!rows.length) {
    return <p className="im2-note">No {side === 'BUY' ? 'buy' : 'sell'} signals right now.</p>;
  }
  return (
    <div className="im2-wrap">
      <table className="im2-tbl">
        <thead>
          <tr>
            <th>Symbol</th><th>Call</th><th className="n">Score</th><th className="n">Entry</th>
            <th className="n">Now</th><th className="n">TP</th><th className="n">SL</th>
            <th className="n">P/L %</th><th className="n">Held</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const s = r.signal || {};
            return (
              <tr key={r.symbol} className="im2-rowlink" onClick={() => nav(`/ai-insights/${r.symbol}?run=1&horizon=TODAY`)}>
                <td className="sym">{r.symbol}</td>
                <td><span className={`im2-call ${callClass(s.state || r.call)}`}>{s.state || r.call}</span></td>
                <td className="n">{r.score ?? '—'}</td>
                <td className="n">{money(s.entry)}</td>
                <td className="n">{money(s.now)}</td>
                <td className="n">{money(s.tp)}</td>
                <td className="n">{money(s.sl)}</td>
                <td className={`n ${(s.pl_pct || 0) > 0 ? 'pos' : (s.pl_pct || 0) < 0 ? 'neg' : ''}`}>{pct(s.pl_pct)}</td>
                <td className="n">{s.held_min != null ? `${Math.round(s.held_min)}m` : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function IntradayV2Board() {
  const [tab, setTab] = useState<'board' | 'log' | 'changes'>('board');
  const board = useApi<any>((s) => api2.intradayBoard(s), [], { refreshMs: 30_000 });
  const logq = useApi<any>((s) => api2.intradayLog('', s), [], { refreshMs: 30_000 });
  const d = board.data;
  if (d && d.status === 'DISABLED') return null;            // flag off

  const sum = (logq.data?.summary) || d?.summary || {};
  const counts = d?.counts || {};
  const health = d?.health || {};
  const warming = d?.status === 'WARMING' || (board.initialLoading && !d);

  const Stat = ({ icon, label, n, cls }: any) => (
    <div className={`im2-stat ${cls}`}>{icon}<div><div className="im2-stat-n">{n ?? '—'}</div>
      <div className="im2-stat-l">{label}</div></div></div>
  );

  return (
    <div className="im2-board">
      {/* Daily summary */}
      <div className="im2-summary">
        <b>Today</b>
        <span>Issued {sum.issued ?? 0}</span><span>Closed {sum.closed ?? 0}</span>
        <span className="pos">Wins {sum.wins ?? 0}</span><span className="neg">Losses {sum.losses ?? 0}</span>
        <span>Win rate {sum.win_rate == null ? '—' : `${sum.win_rate}%`}</span>
        <span>Avg P/L {sum.avg_pl_pct == null ? '—' : `${sum.avg_pl_pct}%`}</span>
        {d?.window_open === false && <span className="im2-closed">signals window closed</span>}
      </div>

      {/* Data health -- how many names got bars this scan, and SPY status. */}
      <div className="im2-health">
        <span>Bars <b>{health.bars_ok ?? '—'}/{health.total ?? '—'}</b> this scan</span>
        <span className={health.spy_status === 'ok' ? 'ok' : 'warn'}>
          SPY bars {health.spy_status || '—'}</span>
        {health.market_stale && (
          <span className="im2-stale-flag">⚠ market data stale — holding off on new calls</span>
        )}
      </div>

      <div className="im2-stats">
        <Stat icon={<TrendingUp size={16} />} label="BUY-rated" n={counts.buy} cls="buy" />
        <Stat icon={<TrendingDown size={16} />} label="SELL-rated" n={counts.sell} cls="sell" />
        <Stat icon={<Minus size={16} />} label="NO EDGE" n={counts.no_edge} cls="noedge" />
        <Stat icon={<ShieldAlert size={16} />} label="WITHHELD" n={counts.withheld} cls="withheld" />
      </div>

      <div className="im2-tabs">
        {(['board', 'log', 'changes'] as const).map((x) => (
          <button key={x} className={tab === x ? 'on' : ''} onClick={() => setTab(x)}>
            {x === 'board' ? 'Signals' : x === 'log' ? 'Signal log' : 'Changes'}
          </button>
        ))}
      </div>

      {warming ? (
        <p className="im2-note"><Loader2 size={12} className="spin" /> First scan running…</p>
      ) : tab === 'board' ? (
        <>
          <h3 className="im2-h">Top Buyers</h3>
          <SignalTable rows={d?.buyers || []} side="BUY" />
          <h3 className="im2-h">Top Sellers</h3>
          <SignalTable rows={d?.sellers || []} side="SELL" />
          <div className="im2-split">
            <div>
              <h3 className="im2-h">NO EDGE (gated out)</h3>
              <div className="im2-chips">
                {(d?.no_edge || []).length ? (d?.no_edge || []).map((r: any) =>
                  <span key={r.symbol} className="im2-chip">{r.symbol} <em>{r.score}</em></span>)
                  : <span className="im2-note">—</span>}
              </div>
            </div>
            <div>
              <h3 className="im2-h">WITHHELD (coverage &lt; 70)</h3>
              <div className="im2-chips">
                {(d?.withheld || []).length ? (d?.withheld || []).map((r: any) =>
                  <span key={r.symbol} className="im2-chip withheld">{r.symbol} <em>{r.coverage_pct}%</em></span>)
                  : <span className="im2-note">—</span>}
              </div>
            </div>
          </div>
        </>
      ) : tab === 'log' ? (
        <SignalLog rows={logq.data?.rows || []} loading={logq.initialLoading} />
      ) : (
        <ChangesView rows={logq.data?.changes || []} />
      )}
      <div className="im2-foot">
        v2: gates (coverage ≥ 70, 10:00–15:30 ET window, SPY-VWAP filter, confluence,
        group agreement, earnings, liquidity) + 2-scan confirmation. Read-only — no orders.
      </div>
    </div>
  );
}

function SignalLog({ rows, loading }: { rows: any[]; loading: boolean }) {
  if (loading && !rows.length) return <p className="im2-note"><Loader2 size={12} className="spin" /> Loading…</p>;
  if (!rows.length) return <p className="im2-note">No signals logged today yet.</p>;
  return (
    <div className="im2-wrap">
      <table className="im2-tbl">
        <thead>
          <tr>
            <th>Symbol</th><th>Call</th><th>Signal</th><th>Status</th><th className="n">Entry</th>
            <th className="n">Now</th><th className="n">Exit</th><th className="n">TP</th><th className="n">SL</th>
            <th className="n">P/L %</th><th className="n">P/L $100</th><th className="n">Score in/now</th><th className="n">Held</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.symbol}-${i}`} className={r.is_open ? '' : 'im2-closed-row'}>
              <td className="sym">{r.symbol}</td>
              <td>
                <span className={`im2-call ${callClass(r.is_open ? r.state : 'NO EDGE')}`}>
                  {r.is_open ? r.state : 'CLOSED'}</span>
                {!r.is_open && r.was_state &&
                  <div className="im2-sub">was {r.was_state} · {r.exit_reason}</div>}
              </td>
              <td className="im2-sub">{t(r.signal_ts)}</td>
              <td className="im2-sub">{t(r.status_ts)}</td>
              <td className="n">{money(r.entry)}</td>
              <td className="n">{money(r.now)}</td>
              <td className="n">{money(r.exit)}</td>
              <td className="n">{money(r.tp)}</td>
              <td className="n">{money(r.sl)}</td>
              <td className={`n ${(r.pl_pct || 0) > 0 ? 'pos' : (r.pl_pct || 0) < 0 ? 'neg' : ''}`}>{pct(r.pl_pct)}</td>
              <td className={`n ${(r.pl_dollar_100 || 0) > 0 ? 'pos' : (r.pl_dollar_100 || 0) < 0 ? 'neg' : ''}`}>
                {r.pl_dollar_100 == null ? '—' : `$${Number(r.pl_dollar_100).toFixed(0)}`}</td>
              <td className="n">{r.score_in ?? '—'} / {r.score_now ?? '—'}</td>
              <td className="n">{r.held_min != null ? `${Math.round(r.held_min)}m` : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChangesView({ rows }: { rows: any[] }) {
  if (!rows.length) return <p className="im2-note">No state changes today yet.</p>;
  return (
    <div className="im2-wrap">
      <table className="im2-tbl">
        <thead><tr><th>Time</th><th>Symbol</th><th>Transition</th><th className="n">P/L %</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className="im2-sub">{t(r.ts)}</td>
              <td className="sym">{r.symbol}</td>
              <td>{r.from || '—'} → <b>{r.to}</b></td>
              <td className={`n ${(r.pl_pct || 0) > 0 ? 'pos' : (r.pl_pct || 0) < 0 ? 'neg' : ''}`}>{pct(r.pl_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
