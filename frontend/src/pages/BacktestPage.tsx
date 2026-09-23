import React, { useState } from 'react';
import { Info, Play } from 'lucide-react';
import { api2 } from '../api/client';
import type { PageContext } from '../App';
import { Panel } from '../components/common';
import { num, pct, signedPct, tone } from '../lib/format';
import { Loading, PageHead, StatusChip, Unavailable } from './shared';

export default function BacktestPage({ ctx }: { ctx: PageContext }) {
  const [symbols, setSymbols] = useState(ctx.symbol);
  const [minScore, setMinScore] = useState(70);
  const [direction, setDirection] = useState('LONG');
  const [holdDays, setHoldDays] = useState(10);
  const [stopPct, setStopPct] = useState(5);
  const [targetPct, setTargetPct] = useState(10);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');

  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    setRunning(true);
    setError(null);
    try {
      const out = await api2.backtest({
        symbols: symbols.split(',').map((s) => s.trim()).filter(Boolean),
        start: start || undefined,
        end: end || undefined,
        min_score: minScore,
        direction,
        hold_days: holdDays,
        stop_pct: stopPct,
        target_pct: targetPct,
      });
      setResult(out);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const stats = result?.stats || {};
  const trades: any[] = result?.trades || [];
  const method = result?.methodology;

  return (
    <div className="page">
      <PageHead
        title="Backtesting"
        subtitle="Walk-forward over real IBKR daily bars. Signals use only bars up to the decision date; entry is the next bar's open."
      />

      <Panel title="Parameters" noBody>
        <form className="filter-bar" style={{ borderBottom: 0 }} onSubmit={run}>
          <label>Symbols
            <input type="text" value={symbols} style={{ width: 170 }}
              onChange={(e) => setSymbols(e.target.value.toUpperCase())}
              placeholder="NVDA, AAPL" />
          </label>
          <label>Direction
            <select className="mini" value={direction}
              onChange={(e) => setDirection(e.target.value)}>
              <option>LONG</option><option>SHORT</option><option>BOTH</option>
            </select>
          </label>
          <label>Min score
            <input type="number" value={minScore} min={0} max={100}
              onChange={(e) => setMinScore(Number(e.target.value))} />
          </label>
          <label>Hold (bars)
            <input type="number" value={holdDays} min={1} max={120}
              onChange={(e) => setHoldDays(Number(e.target.value))} />
          </label>
          <label>Stop %
            <input type="number" value={stopPct} min={0.5} step={0.5}
              onChange={(e) => setStopPct(Number(e.target.value))} />
          </label>
          <label>Target %
            <input type="number" value={targetPct} min={0.5} step={0.5}
              onChange={(e) => setTargetPct(Number(e.target.value))} />
          </label>
          <label>From
            <input type="date" value={start} style={{ width: 118 }}
              onChange={(e) => setStart(e.target.value)} />
          </label>
          <label>To
            <input type="date" value={end} style={{ width: 118 }}
              onChange={(e) => setEnd(e.target.value)} />
          </label>
          <button className="ghost-btn on" type="submit" disabled={running}>
            <Play size={11} /> {running ? 'Running…' : 'Run backtest'}
          </button>
        </form>
      </Panel>

      {error && <Panel title="Error"><Unavailable status="ERROR" detail={error} /></Panel>}

      {running && <Panel title="Running"><Loading label="Replaying bars…" /></Panel>}

      {result && !running && (
        <>
          {method && (
            <div className="insight-banner">
              <Info size={14} />
              <div>
                <b>Point-in-time.</b> {method.point_in_time} Signal:{' '}
                {method.signal}. Components that cannot be replayed historically
                are excluded: {Object.keys(method.not_replayable || {}).join(', ')}.
              </div>
            </div>
          )}

          {result.status !== 'OK' ? (
            <Panel title="Result">
              <Unavailable status={result.status} detail={result.detail} />
            </Panel>
          ) : (
            <>
              <div className="stat-grid">
                <Stat label="Trades" value={stats.trades} />
                <Stat label="Win rate" value={`${stats.win_rate}%`}
                  tone={stats.win_rate >= 50 ? 'pos' : 'neg'} />
                <Stat label="Avg return" value={signedPct(stats.avg_return)}
                  tone={tone(stats.avg_return)} />
                <Stat label="Net return" value={signedPct(stats.net_return)}
                  tone={tone(stats.net_return)} />
                <Stat label="Profit factor"
                  value={stats.profit_factor ?? '--'}
                  tone={stats.profit_factor >= 1 ? 'pos' : 'neg'} />
                <Stat label="Max drawdown" value={signedPct(stats.max_drawdown)} tone="neg" />
                <Stat label="Avg MFE" value={signedPct(stats.avg_mfe)} tone="pos" />
                <Stat label="Avg MAE" value={signedPct(stats.avg_mae)} tone="neg" />
              </div>

              <Panel title={`Trades (${trades.length})`} noBody>
                <div className="tbl-scroll" style={{ maxHeight: 420 }}>
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Ticker</th><th>Side</th><th>Signal</th><th>Entry date</th>
                        <th>Exit date</th><th className="r">Score</th>
                        <th className="r">Entry</th><th className="r">Exit</th>
                        <th className="r">Return</th><th className="r">MFE</th>
                        <th className="r">MAE</th><th>Exit</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trades.slice().reverse().map((t, i) => (
                        <tr key={i}>
                          <td><b>{t.symbol}</b></td>
                          <td style={{ color: t.side === 'LONG' ? 'var(--green)' : 'var(--red)' }}>
                            {t.side}
                          </td>
                          <td className="num">{t.signal_date}</td>
                          <td className="num">{t.entry_date}</td>
                          <td className="num">{t.exit_date}</td>
                          <td className="num r">{t.score}</td>
                          <td className="num r">{num(t.entry)}</td>
                          <td className="num r">{num(t.exit)}</td>
                          <td className={`num r ${tone(t.return_pct)}`}>
                            {signedPct(t.return_pct)}
                          </td>
                          <td className="num r pos">{signedPct(t.mfe_pct)}</td>
                          <td className="num r neg">{signedPct(t.mae_pct)}</td>
                          <td>
                            <span className={`badge ${t.exit_reason === 'TARGET' ? 'green'
                              : t.exit_reason === 'STOP' ? 'red' : 'gray'}`}>
                              {t.exit_reason}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="hint">
                  {method?.costs}. {method?.confidence_gate}
                </div>
              </Panel>
            </>
          )}

          {result.unavailable?.length > 0 && (
            <Panel title="Symbols skipped" noBody>
              <div className="kv">
                {result.unavailable.map((u: any) => (
                  <div className="kv-row" key={u.symbol}>
                    <span className="k">{u.symbol}</span>
                    <StatusChip status={u.status} />
                  </div>
                ))}
              </div>
            </Panel>
          )}
        </>
      )}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: any; tone?: string }) {
  return (
    <div className="panel stat-card">
      <div className="st-label">{label}</div>
      <div className={`st-value ${tone || ''}`}>{value ?? '--'}</div>
    </div>
  );
}
