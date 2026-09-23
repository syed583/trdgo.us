import React from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { ArrowRight, Bell, Loader2, RefreshCw, TrendingDown, TrendingUp } from 'lucide-react';
import { api2 } from '../api/client';
import type { PageContext } from '../App';
import { useApi } from '../hooks/useApi';
import { LogoMark, Panel, Sparkline, ratingColor } from '../components/common';
import { money, num, signed, signedPct, tone } from '../lib/format';
import {
  ErrorState, Loading, PageHead, Unavailable,
} from './shared';

export default function DashboardPage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();
  const data = useApi<any>((s) => api2.dashboard(s), [], { refreshMs: 90_000 });
  const d = data.data;

  const open = (symbol: string) => navigate(`/earnings/${symbol}${ctx.search}`);

  return (
    <div className="page">
      <PageHead
        title="Dashboard"
        subtitle={d ? <>Market {d.market?.label} · {d.market?.time_et}</> : 'Loading…'}
        right={
          <button className="ghost-btn" onClick={data.refresh}>
            <RefreshCw size={12} className={data.loading ? 'spin' : undefined} />
            Refresh
          </button>
        }
      />

      {data.error ? <ErrorState error={data.error} />
        : data.initialLoading || !d ? <Loading label="Assembling dashboard…" />
          : (
            <>
              {/* The provider strip lived here and repeated eleven green OK
                  chips above the content on every load. Provider health is
                  still one click away in Settings, and the topbar badge turns
                  amber when something is actually degraded -- which is the
                  only time it is worth the space. */}
              <div className="dash-grid">
                <Panel
                  title="Market Overview"
                  noBody
                  right={<NavLink className="panel-link" to={`/market${ctx.search}`}>
                    Open <ArrowRight size={11} />
                  </NavLink>}
                >
                  <div className="dash-idx">
                    {(d.overview?.indices || []).map((i: any) => (
                      <button key={i.symbol} className="dash-idx-card"
                        onClick={() => open(i.symbol)}>
                        <div className="ov-label">{i.label}</div>
                        <div className="ov-price">{money(i.price)}</div>
                        <div className={`ov-chg ${tone(i.change_percent)}`}>
                          {signedPct(i.change_percent)}
                        </div>
                        <Sparkline points={i.spark || []}
                          color={tone(i.change_percent) === 'neg' ? 'var(--red)' : 'var(--green)'}
                          width={78} height={22} />
                      </button>
                    ))}
                  </div>
                </Panel>

                <Panel
                  title="Top Bullish Setups"
                  icon={<TrendingUp size={12} />}
                  noBody
                  right={<NavLink className="panel-link" to={`/scanner${ctx.search}`}>
                    Scanner <ArrowRight size={11} />
                  </NavLink>}
                >
                  <SetupList rows={d.bullish} onOpen={open} pending={d.scoring?.pending} />
                </Panel>

                <Panel
                  title="Top Bearish Setups"
                  icon={<TrendingDown size={12} />}
                  noBody
                >
                  <SetupList rows={d.bearish} onOpen={open} pending={d.scoring?.pending} />
                </Panel>

                <Panel
                  title="Upcoming Earnings"
                  noBody
                  right={<NavLink className="panel-link" to={`/earnings-calendar${ctx.search}`}>
                    Calendar <ArrowRight size={11} />
                  </NavLink>}
                >
                  {d.earnings?.rows?.length ? (
                    <table className="tbl">
                      <thead>
                        <tr><th>Ticker</th><th>Date</th><th>Timing</th><th className="r">Price</th></tr>
                      </thead>
                      <tbody>
                        {d.earnings.rows.slice(0, 6).map((r: any) => (
                          <tr key={r.symbol} className="clickable" onClick={() => open(r.symbol)}>
                            <td><b>{r.symbol}</b></td>
                            <td className="num">{r.date_label}</td>
                            <td><span className="badge blue">{r.reporting_time || 'TBD'}</span></td>
                            <td className="num r">{money(r.price)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <Unavailable
                      status={d.earnings?.empty_window
                        ? 'NO_DATA' : d.earnings?.source?.status}
                      detail={d.earnings?.empty_detail
                        || d.earnings?.source?.detail}
                      required={d.earnings?.source?.required_provider}
                      compact
                    />
                  )}
                </Panel>

                <Panel
                  title="Watchlist"
                  noBody
                  right={<NavLink className="panel-link" to={`/watchlist${ctx.search}`}>
                    Manage <ArrowRight size={11} />
                  </NavLink>}
                >
                  {d.watchlist?.rows?.length ? (
                    <table className="tbl">
                      <thead>
                        <tr><th>Ticker</th><th className="r">Price</th><th className="r">Change</th></tr>
                      </thead>
                      <tbody>
                        {d.watchlist.rows.slice(0, 8).map((r: any) => (
                          <tr key={r.symbol} className="clickable" onClick={() => open(r.symbol)}>
                            <td><b>{r.symbol}</b></td>
                            <td className="num r">{money(r.price)}</td>
                            <td className={`num r ${tone(r.change_percent)}`}>
                              {signedPct(r.change_percent)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div className="state" style={{ minHeight: 90 }}>
                      <span className="state-title">Watchlist empty</span>
                      <NavLink className="ghost-btn" to={`/watchlist${ctx.search}`}>
                        Add symbols
                      </NavLink>
                    </div>
                  )}
                </Panel>

                <Panel
                  title="Alerts"
                  icon={<Bell size={12} />}
                  noBody
                  right={<NavLink className="panel-link" to={`/alerts${ctx.search}`}>
                    Manage <ArrowRight size={11} />
                  </NavLink>}
                >
                  {d.alerts?.rows?.length ? (
                    <table className="tbl">
                      <thead>
                        <tr><th>Ticker</th><th>Condition</th><th className="r">Now</th><th>State</th></tr>
                      </thead>
                      <tbody>
                        {d.alerts.rows.slice(0, 6).map((r: any) => (
                          <tr key={r.id} className={r.triggered ? 'alert-hit' : ''}>
                            <td><b>{r.symbol}</b></td>
                            <td>{r.kind_label} {r.comparator} {num(r.threshold)}</td>
                            <td className="num r">
                              {r.current_value != null ? num(r.current_value) : '--'}
                            </td>
                            <td>
                              {r.triggered
                                ? <span className="badge green">HIT</span>
                                : <span className="badge gray">armed</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div className="state" style={{ minHeight: 90 }}>
                      <span className="state-title">No alerts configured</span>
                      <NavLink className="ghost-btn" to={`/alerts${ctx.search}`}>
                        Create one
                      </NavLink>
                    </div>
                  )}
                </Panel>
              </div>

              {d.score_basis && <div className="hint">{d.score_basis}</div>}
            </>
          )}
    </div>
  );
}

function SetupList({
  rows, onOpen, pending,
}: { rows: any[]; onOpen: (s: string) => void; pending?: string[] | null }) {
  if (!rows?.length) {
    // A cold composite needs one SEC fetch per symbol, so on a fresh start
    // these panels are waiting, not empty. Saying "unavailable" made a normal
    // warm-up look like a failure.
    if (pending?.length) {
      return (
        <div className="state" style={{ minHeight: 60, padding: 14 }}>
          <Loader2 size={17} className="spin" />
          <span className="state-title">Scoring {pending.length} symbol
            {pending.length === 1 ? '' : 's'}…</span>
          <span>Setups appear as each composite finishes.</span>
        </div>
      );
    }
    return <Unavailable status="DATA_UNAVAILABLE"
      detail="No scored symbols available" compact />;
  }
  return (
    <div className="setup-list">
      {rows.map((c) => (
        <button key={c.symbol} className="setup-row" onClick={() => onOpen(c.symbol)}>
          <LogoMark symbol={c.symbol} className="tc-logo" />
          <div className="setup-id">
            <b>{c.symbol}</b>
            <span>{c.name}</span>
          </div>
          <div className="setup-px">
            <b>{money(c.price)}</b>
            <span className={tone(c.change_percent)}>{signedPct(c.change_percent)}</span>
          </div>
          <div className="setup-score" style={{ color: ratingColor(c.rating) }}>
            <b>{c.score != null ? signed(c.score, 0) : '--'}</b>
            <span>{c.rating || 'Unscored'}</span>
          </div>
        </button>
      ))}
    </div>
  );
}

