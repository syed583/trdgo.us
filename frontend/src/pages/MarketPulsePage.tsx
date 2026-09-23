import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Activity, Gauge, Globe, Layers, RefreshCw, TrendingDown, TrendingUp,
} from 'lucide-react';
import { api2 } from '../api/client';
import type { PageContext } from '../App';
import { useApi } from '../hooks/useApi';
import { Panel } from '../components/common';
import { money, num, signedPct, tone } from '../lib/format';
import { ErrorState, Loading } from './shared';

/**
 * Market Overview.
 *
 * Every figure is measured from quotes this app fetched, and the page says so
 * where that matters: breadth names its universe, and each ETF standing in for
 * an index is marked as a proxy rather than labelled with the index's name.
 */

function pct(value: number | null | undefined): string {
  if (value == null) return '--';
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

/** An index card: level, move, and whether it is the index or a stand-in. */
function IndexCard({ row }: { row: any }) {
  const dir = tone(row.change_percent);
  return (
    <div className="mo-index">
      <span className="mo-index-label">
        {row.label}
        {row.is_proxy && (
          <em title={`Tracking ETF ${row.symbol}, not the index itself`}>
            {row.symbol}
          </em>
        )}
      </span>
      {row.price == null ? (
        <b className="mo-index-value mute">--</b>
      ) : (
        <>
          <b className="mo-index-value">{num(row.price, 2)}</b>
          <span className={`mo-index-change ${dir}`}>
            {row.change != null
              && `${row.change > 0 ? '▲' : '▼'} ${Math.abs(row.change).toFixed(2)} `}
            ({pct(row.change_percent)})
          </span>
        </>
      )}
    </div>
  );
}

/** Sector rows as a signed bar, strongest first. */
function SectorBars({ sectors }: { sectors: any[] }) {
  const peak = useMemo(
    () => Math.max(0.5, ...sectors.map((s) => Math.abs(s.change_percent || 0))),
    [sectors],
  );
  return (
    <div className="mo-sectors">
      {sectors.map((s) => {
        const value = s.change_percent;
        const width = value == null ? 0 : Math.abs(value) / peak * 50;
        return (
          <div className="mo-sector" key={s.symbol}>
            <span className="mo-sector-name" title={s.symbol}>{s.label}</span>
            <span className="mo-sector-track">
              <i className={value != null && value < 0 ? 'neg' : 'pos'}
                style={value != null && value < 0
                  ? { right: '50%', width: `${width}%` }
                  : { left: '50%', width: `${width}%` }} />
            </span>
            <b className={tone(value)}>{pct(value)}</b>
          </div>
        );
      })}
    </div>
  );
}

/** Constituents as tiles, sized by nothing and coloured by their move. */
function Heatmap({ groups, onPick }: {
  groups: any[]; onPick: (symbol: string) => void;
}) {
  const shade = (value: number | null) => {
    if (value == null) return 'var(--panel-2)';
    const clamped = Math.max(-3, Math.min(3, value));
    const strength = Math.abs(clamped) / 3;
    // Two ramps rather than one diverging scale: a red that fades to grey and
    // a green that fades to grey read faster than a single hue rotation.
    const hue = clamped >= 0 ? 152 : 4;
    return `hsl(${hue} ${Math.round(28 + strength * 44)}% ${Math.round(34 - strength * 12)}%)`;
  };

  return (
    <div className="mo-heat">
      {groups.map((g) => (
        <div className="mo-heat-group" key={g.sector}>
          <span className="mo-heat-title">
            {g.sector}
            <em className={tone(g.average)}>{pct(g.average)}</em>
          </span>
          <div className="mo-heat-tiles">
            {g.members.map((m: any) => (
              <button className="mo-heat-tile" key={m.symbol}
                style={{ background: shade(m.change_percent) }}
                onClick={() => onPick(m.symbol)}
                title={`${m.symbol} ${money(m.price)} ${pct(m.change_percent)}`}>
                <b>{m.symbol}</b>
                <i>{pct(m.change_percent)}</i>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/** A half-dial for the composite sentiment reading. */
function SentimentDial({ value, label }: {
  value: number | null; label: string | null;
}) {
  const v = value == null ? 50 : Math.max(0, Math.min(100, value));
  const angle = (v - 50) / 50 * 90;
  const colour = v >= 60 ? 'var(--green)' : v <= 40 ? 'var(--red)' : 'var(--amber)';
  return (
    <div className="mo-dial">
      <svg viewBox="0 0 120 68" width="160" height="90">
        <path d="M 10 60 A 50 50 0 0 1 40 14" fill="none"
          stroke="var(--red)" strokeWidth="9" strokeLinecap="round" />
        <path d="M 46 11 A 50 50 0 0 1 74 11" fill="none"
          stroke="var(--amber)" strokeWidth="9" strokeLinecap="round" />
        <path d="M 80 14 A 50 50 0 0 1 110 60" fill="none"
          stroke="var(--green)" strokeWidth="9" strokeLinecap="round" />
        <g transform={`rotate(${angle} 60 60)`}>
          <line x1="60" y1="60" x2="60" y2="24" stroke={colour}
            strokeWidth="3" strokeLinecap="round" />
        </g>
        <circle cx="60" cy="60" r="4.5" fill={colour} />
      </svg>
      <b style={{ color: colour }}>{value ?? '--'}<small>/100</small></b>
      <i>{label || 'No reading'}</i>
    </div>
  );
}

export default function MarketPulsePage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();
  const [tick, setTick] = useState(0);
  const pulse = useApi<any>((s) => api2.marketPulse(s), [tick], {
    refreshMs: 60000,
  });
  const d = pulse.data;

  const open = (symbol: string) => navigate(`/earnings/${symbol}${ctx.search}`);

  if (pulse.error) {
    return (
      <div className="page">
        <Panel title="Market Overview"><ErrorState error={pulse.error} /></Panel>
      </div>
    );
  }
  // A payload can arrive before the first pass has finished. Treat that as
  // loading rather than rendering a page of empty panels, and poll until it
  // settles -- the build is running, it just has not landed yet.
  const building = d?.status === 'BUILDING';
  useEffect(() => {
    if (!building) return undefined;
    const t = setTimeout(() => setTick((n) => n + 1), 6000);
    return () => clearTimeout(t);
  }, [building, tick]);

  if (pulse.initialLoading || !d || building) {
    return (
      <div className="page">
        <Panel title="Market Overview">
          <Loading />
          <div className="hint" style={{ textAlign: 'center' }}>
            Pricing indices, sectors and the breadth universe. The first pass
            takes a moment; later reads are cached.
          </div>
        </Panel>
      </div>
    );
  }

  const b = d.breadth || {};
  const s = d.sentiment || {};

  return (
    <div className="page">
      <div className="mo-head">
        <div>
          <h1>Market Overview</h1>
          <p>
            Indices, sector performance, breadth and global proxies — measured
            from live quotes.
          </p>
        </div>
        <div className="mo-head-right">
          <span className={`mo-live ${d.market?.is_open ? 'on' : ''}`}>
            <i />{d.market?.label || 'Market'}
          </span>
          <button className="ghost-btn" onClick={pulse.refresh}>
            <RefreshCw size={12} className={pulse.loading ? 'spin' : undefined} />
            Refresh
          </button>
        </div>
      </div>

      <div className="mo-indices">
        {(d.indices || []).map((row: any) => (
          <IndexCard row={row} key={row.label} />
        ))}
      </div>

      <div className="mo-grid">
        <Panel title="Sector Performance" icon={<Layers size={13} />}>
          <SectorBars sectors={d.sectors || []} />
          <div className="hint">
            The eleven SPDR sector ETFs, today&apos;s move, strongest first.
          </div>
        </Panel>

        <Panel title="Market Heatmap" icon={<Activity size={13} />} noBody>
          <Heatmap groups={d.heatmap || []} onPick={open} />
          <div className="hint">
            {b.detail} Click a tile to open that company.
          </div>
        </Panel>

        <div className="mo-side">
          <Panel title="Market Breadth" icon={<TrendingUp size={13} />}>
            <div className="mo-breadth-bar">
              <i className="pos" style={{ width: `${b.advancing_percent ?? 0}%` }} />
              <i className="neg" style={{ width: `${100 - (b.advancing_percent ?? 0)}%` }} />
            </div>
            <div className="mo-breadth-legend">
              <span className="pos">{b.advancing} advancing ({b.advancing_percent}%)</span>
              <span className="neg">{b.declining} declining</span>
            </div>
            <div className="hint">{b.detail}</div>
          </Panel>

          <Panel title="Key Market Indicators" icon={<Gauge size={13} />}>
            <div className="mo-rows">
              {(d.indicators || []).map((r: any) => (
                <div className="mo-row" key={r.symbol}>
                  <span>{r.label}<em>{r.symbol}</em></span>
                  <b>{r.price != null ? num(r.price, 2) : '--'}</b>
                  <u className={tone(r.change_percent)}>{pct(r.change_percent)}</u>
                </div>
              ))}
            </div>
            <div className="hint">
              Every row is a tracking ETF, not the underlying: no configured
              provider quotes a yield, the dollar index or a crude contract
              directly. The instrument is named beside each label.
            </div>
          </Panel>
        </div>
      </div>

      <div className="mo-grid-3">
        <Panel title="Top Gainers" icon={<TrendingUp size={13} />} noBody>
          <table className="tbl">
            <thead>
              <tr><th>Symbol</th><th className="r">Price</th><th className="r">Change</th></tr>
            </thead>
            <tbody>
              {(d.gainers || []).map((r: any) => (
                <tr key={r.symbol} className="clickable" onClick={() => open(r.symbol)}>
                  <td><b className="mo-sym">{r.symbol}</b></td>
                  <td className="num r">{money(r.price)}</td>
                  <td className="num r pos">{pct(r.change_percent)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel title="Top Losers" icon={<TrendingDown size={13} />} noBody>
          <table className="tbl">
            <thead>
              <tr><th>Symbol</th><th className="r">Price</th><th className="r">Change</th></tr>
            </thead>
            <tbody>
              {(d.losers || []).map((r: any) => (
                <tr key={r.symbol} className="clickable" onClick={() => open(r.symbol)}>
                  <td><b className="mo-sym">{r.symbol}</b></td>
                  <td className="num r">{money(r.price)}</td>
                  <td className="num r neg">{pct(r.change_percent)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel title="Global Markets" icon={<Globe size={13} />}>
          <div className="mo-rows">
            {(d.global || []).map((r: any) => (
              <div className="mo-row" key={r.symbol}>
                <span>{r.label}<em>{r.symbol}</em></span>
                <b>{r.price != null ? num(r.price, 2) : '--'}</b>
                <u className={tone(r.change_percent)}>{pct(r.change_percent)}</u>
              </div>
            ))}
          </div>
          <div className="hint">
            Country ETFs listed in the US, standing in for their home index —
            they trade on US hours and carry tracking error.
          </div>
        </Panel>
      </div>

      <div className="mo-grid-2">
        <Panel title="Market Sentiment" icon={<Gauge size={13} />}>
          {s.status !== 'OK' ? (
            <div className="hint">{s.detail || 'No sentiment reading.'}</div>
          ) : (
            <div className="mo-sentiment">
              <SentimentDial value={s.value} label={s.label} />
              <div className="mo-sent-parts">
                {(s.components || []).map((c: any) => (
                  <div className="mo-sent-part" key={c.name}>
                    <span>{c.name}</span>
                    <b>{c.score}</b>
                    <i>{c.detail}</i>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="hint">{s.detail}</div>
        </Panel>

        <Panel title="Options Put / Call" icon={<Activity size={13} />}>
          {d.put_call?.status !== 'OK' ? (
            <div className="hint">
              The options tape is not available for this session.
            </div>
          ) : (
            <>
              <div className="mo-pc">
                <b className={((d.put_call.put_call_ratio ?? 1) < 1) ? 'pos' : 'neg'}>
                  {d.put_call.put_call_ratio}
                </b>
                <span>{d.put_call.sentiment}</span>
              </div>
              <div className="mo-rows">
                <div className="mo-row">
                  <span>Call premium</span>
                  <b>${((d.put_call.call_premium || 0) / 1e9).toFixed(2)}B</b>
                </div>
                <div className="mo-row">
                  <span>Put premium</span>
                  <b>${((d.put_call.put_premium || 0) / 1e9).toFixed(2)}B</b>
                </div>
              </div>
              <div className="hint">
                Premium-weighted across the watched tape for{' '}
                {d.put_call.session}, not contract counts — a thousand cheap
                lottery calls are not the conviction one large block is.
              </div>
            </>
          )}
        </Panel>
      </div>

      <div className="hint">{d.detail}</div>
    </div>
  );
}
