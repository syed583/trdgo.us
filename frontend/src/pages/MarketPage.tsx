import React from 'react';
import { useNavigate } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';
import { api2 } from '../api/client';
import type { PageContext } from '../App';
import { useApi } from '../hooks/useApi';
import { Panel, Sparkline, StateBlock } from '../components/common';
import { compact, money, num, signedPct, tone } from '../lib/format';
import { ErrorState, Loading, PageHead, StatusChip, Unavailable } from './shared';

const TREND_TONE: Record<string, string> = {
  STRONG_UPTREND: 'var(--green)',
  UPTREND: 'var(--green)',
  MIXED: 'var(--amber)',
  DOWNTREND: 'var(--red)',
};

export default function MarketPage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();
  const data = useApi<any>((s) => api2.marketOverview(s), [], { refreshMs: 60_000 });
  const d = data.data;

  const open = (symbol: string) => navigate(`/earnings/${symbol}${ctx.search}`);

  return (
    <div className="page">
      <PageHead
        title="Market Overview"
        subtitle="Broad indices and the eleven SPDR sectors, live from IBKR."
        right={
          <button className="ghost-btn" onClick={data.refresh}>
            <RefreshCw size={12} className={data.loading ? 'spin' : undefined} />
            Refresh
          </button>
        }
      />

      {data.error ? <ErrorState error={data.error} />
        : data.initialLoading || !d ? <Loading />
          : (
            <>
              <div className="ov-grid">
                {d.indices.map((c: any) => (
                  <IndexCard key={c.symbol} card={c} onOpen={open} />
                ))}
                {d.vix && (
                  <div className="panel ov-card" title="CBOE Volatility Index">
                    <div className="ov-label">VIX <span className="ov-sub">Volatility</span></div>
                    <div className="ov-price">{num(d.vix.value)}</div>
                    <div className={`ov-chg ${tone(d.vix.change_percent)}`}>
                      {signedPct(d.vix.change_percent)}
                    </div>
                    <Sparkline
                      points={d.vix.spark}
                      color={tone(d.vix.change_percent) === 'neg' ? 'var(--red)' : 'var(--green)'}
                      width={92} height={26}
                    />
                  </div>
                )}
              </div>

              <div className="two-col">
                <Panel
                  title="Sector Performance"
                  noBody
                  right={
                    <span className="hint" style={{ padding: 0 }}>
                      {d.breadth.advancing} of {d.breadth.measured} advancing
                    </span>
                  }
                >
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Sector</th><th>ETF</th>
                        <th className="r">Price</th>
                        <th className="r">Change</th>
                        <th>Trend</th>
                        <th>EMA 20 / 50 / 200</th>
                        <th className="r">RSI</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.sectors.map((s: any) => (
                        <tr key={s.symbol} className="clickable"
                          onClick={() => open(s.symbol)}>
                          <td>{s.label}</td>
                          <td className="num">{s.symbol}</td>
                          <td className="num r">{money(s.price)}</td>
                          <td className={`num r ${tone(s.change_percent)}`}>
                            {signedPct(s.change_percent)}
                          </td>
                          <td style={{ color: TREND_TONE[s.state] || 'var(--text-dim)' }}>
                            {s.trend || '--'}
                          </td>
                          <td><EmaDots row={s} /></td>
                          <td className="num r">{s.rsi ?? '--'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Panel>

                <div className="stack">
                  <Panel title="Sector Leaders" noBody>
                    {d.leaders.length ? (
                      <div className="kv">
                        {d.leaders.map((s: any) => (
                          <button className="kv-row clickable" key={s.symbol}
                            onClick={() => open(s.symbol)}>
                            <span className="k">{s.label}</span>
                            <span className="v pos">{signedPct(s.change_percent)}</span>
                          </button>
                        ))}
                      </div>
                    ) : <Unavailable status="DATA_UNAVAILABLE" compact />}
                  </Panel>

                  <Panel title="Sector Laggards" noBody>
                    {d.laggards.length ? (
                      <div className="kv">
                        {d.laggards.map((s: any) => (
                          <button className="kv-row clickable" key={s.symbol}
                            onClick={() => open(s.symbol)}>
                            <span className="k">{s.label}</span>
                            <span className="v neg">{signedPct(s.change_percent)}</span>
                          </button>
                        ))}
                      </div>
                    ) : <Unavailable status="DATA_UNAVAILABLE" compact />}
                  </Panel>

                  <Panel title="Breadth" noBody>
                    <div className="kv">
                      <div className="kv-row">
                        <span className="k">Sectors advancing</span>
                        <span className="v pos">{d.breadth.advancing}</span>
                      </div>
                      <div className="kv-row">
                        <span className="k">Sectors declining</span>
                        <span className="v neg">{d.breadth.declining}</span>
                      </div>
                      <div className="kv-row">
                        <span className="k">Measured</span>
                        <span className="v">{d.breadth.measured}/{d.breadth.total}</span>
                      </div>
                    </div>
                    <div className="hint">
                      Sector cards use the SPDR ETF as the tradable proxy.
                    </div>
                  </Panel>
                </div>
              </div>
            </>
          )}
    </div>
  );
}

function IndexCard({ card, onOpen }: { card: any; onOpen: (s: string) => void }) {
  const t = tone(card.change_percent);
  return (
    <button className="panel ov-card clickable" onClick={() => onOpen(card.symbol)}
      title={card.note}>
      <div className="ov-label">
        {card.label} <span className="ov-sub">{card.symbol}</span>
      </div>
      <div className="ov-price">{money(card.price)}</div>
      <div className={`ov-chg ${t}`}>{signedPct(card.change_percent)}</div>
      <Sparkline
        points={card.spark}
        color={t === 'neg' ? 'var(--red)' : 'var(--green)'}
        width={92} height={26}
      />
      {card.trend && (
        <div className="ov-trend" style={{ color: TREND_TONE[card.state] }}>
          {card.trend}
        </div>
      )}
    </button>
  );
}

function EmaDots({ row }: { row: any }) {
  const dot = (v: boolean | null, label: string) => (
    <span
      key={label}
      className="ema-dot"
      title={`${label}: ${v === null ? 'unknown' : v ? 'above' : 'below'}`}
      style={{
        background: v === null ? 'var(--gray-bar)'
          : v ? 'var(--green)' : 'var(--red)',
      }}
    />
  );
  return (
    <span className="ema-dots">
      {dot(row.above_ema20, 'EMA 20')}
      {dot(row.above_ema50, 'EMA 50')}
      {dot(row.above_ema200, 'EMA 200')}
    </span>
  );
}
