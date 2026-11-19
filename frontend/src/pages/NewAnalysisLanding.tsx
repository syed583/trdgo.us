import React from 'react';
import { NavLink } from 'react-router-dom';
import { CalendarDays, Gauge, Layers, Target, Zap } from 'lucide-react';
import {
  Cell, Pie, PieChart, ResponsiveContainer, Tooltip,
} from 'recharts';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import analysisHero from '../assets/analysis-hero.webp';
import '../new-dashboard.css';

/**
 * The Analysis Intraday LANDING in the redesigned shell (Figma frame 2:2).
 *
 * THE FRAME IS NAMED "Market intelligence dashboard" AND IS NOT THE DASHBOARD. Its panels - Top
 * Movers, the index list, the earnings and news list - are the ones on THIS page's landing, not on
 * /dashboard. It was mapped by its name once; the owner corrected it, and the panels settle it.
 *
 * NOTHING HERE FETCHES ANYTHING. Both the controls that start an analysis and the three data panels
 * are PASSED IN, already built by InsightsPage from the components it has always used. This file is
 * layout and styling only. That is deliberate: a redesigned landing that re-implemented the panels
 * would be a second copy of the page's data logic, and the copy is the one that quietly stops
 * matching the original. Restyling is done in CSS, against the panels' own class names.
 *
 * OPTIONS FLOW, MARKET SENTIMENT AND SECTOR PERFORMANCE ARE BUILT HERE. They were left out of the
 * first version on the grounds that the live landing did not have them - which was the rule applied
 * backwards. "Leave out what we cannot measure" is not "show only what this page already showed";
 * all three are measured, and they drive Market Overview and Unusual Activity. Two requests cover
 * them for the whole page, both server-cached, neither per row.
 *
 * NOT built: the frame's one-symbol detail card (price, chart, score, RSI, ATR). This landing has no
 * selected symbol, picking one for the reader would be arbitrary, and the analysed view the search
 * leads to IS that card in full - with a quote and a chart request that would otherwise land on
 * every visit to the busiest page here. Nor "150+ Analytical Signals" or "85% Backtest Coverage":
 * nothing measures either, and a claim a reader cannot check is the thing this redesign removed.
 *
 * Only the IDLE landing changes. The scanning view and the analysed result are untouched, so a
 * search started here behaves exactly as it does today.
 */
export default function NewAnalysisLanding({
  ctx, controls, panels,
}: { ctx: PageContext; controls: React.ReactNode; panels: React.ReactNode }) {
  // Two page-level reads, both server-cached. The index strip reuses the shell's own indices, so it
  // costs nothing extra.
  const pulse = useApi<any>((sig) => api2.marketPulse(sig), [], { refreshMs: 300_000 });
  const flow = useApi<any>((sig) => api2.flowMarketSummary(sig), [], { refreshMs: 300_000 });

  const idx: any[] = (ctx.indices.data?.indices || []).slice(0, 4);
  const sectors: any[] = (pulse.data?.sectors || [])
    .filter((x: any) => x && x.change_percent != null)
    .slice(0, 6);
  const sent = pulse.data?.sentiment || null;
  const pc = pulse.data?.put_call || null;

  /** Green for a rise, red for a fall, neither for flat - +0.00% is not a gain. */
  const tone = (v: any) => {
    const n = Number(v);
    if (!Number.isFinite(n) || n === 0) return 'flat';
    return n > 0 ? 'up' : 'down';
  };
  const pctTxt = (v: any) =>
    (v === null || v === undefined ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`);
  const money0 = (v: any) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
    return `$${n.toFixed(0)}`;
  };

  // The gauge the frame draws: a score out of 100, as a half-ring.
  const score = sent?.score != null ? Math.max(0, Math.min(100, Number(sent.score))) : null;
  const gauge = score == null ? [] : [
    { name: 'score', value: score, fill: score >= 60 ? 'var(--nu-up)' : score <= 40 ? 'var(--nu-down)' : 'var(--nu-warn)' },
    { name: 'rest', value: 100 - score, fill: 'var(--nu-line)' },
  ];

  return (
    <div className="nd nal">
      <section className="nd-hero nal-hero">
        <img className="nd-hero-img" src={analysisHero} alt="" aria-hidden="true" loading="eager" />

        <div className="nd-hero-left">
          <span className="nd-eyebrow">US Market Intelligence Platform</span>
          <h1>Trade Smarter with<br />Real Data &amp; <em>AI Insights</em></h1>
          <p>Earnings · Options flow · Technicals · Sentiment, read for one ticker at a time.</p>
          <div className="nal-controls">{controls}</div>
        </div>

        {/* Links to pages that exist. No figure is claimed on this artwork: the four cards the design
            painted into the image were replaced by real ones on the old landing, and a landing that
            states a number the server does not measure is the thing we removed, not a thing to add. */}
        <div className="nd-hero-right nal-feats">
          <NavLink to="/earnings-calendar" className="nd-hero-feat">
            <span className="nd-feat-ico violet"><CalendarDays size={18} /></span>
            <span><b>Earnings</b><i>Dates, estimates and surprises</i></span>
          </NavLink>
          <NavLink to="/unusual-activity" className="nd-hero-feat">
            <span className="nd-feat-ico amber"><Layers size={18} /></span>
            <span><b>Options Flow</b><i>Unusual contracts and disparity</i></span>
          </NavLink>
          <NavLink to="/strategy-trades" className="nd-hero-feat">
            <span className="nd-feat-ico green"><Target size={18} /></span>
            <span><b>Strategy Trades</b><i>Tracked entries, stops and exits</i></span>
          </NavLink>
          <NavLink to="/smart-move/engine20" className="nd-hero-feat">
            <span className="nd-feat-ico blue"><Zap size={18} /></span>
            <span><b>Smart Money Move</b><i>Calls before the window closes</i></span>
          </NavLink>
        </div>
      </section>

      {/* ------------------------------------------------------------ index strip */}
      {!!idx.length && (
        <div className="nal-strip">
          {idx.map((r: any) => (
            <div className="nal-strip-item" key={r.label}>
              <span className="nal-strip-k">{r.label}</span>
              {/* /api/indices carries `value`; the pulse payload calls it `price`. Read both,
                  value first, so the strip matches the panel beside it rather than showing a dash. */}
              <b>{(r.value ?? r.price) != null
                ? Number(r.value ?? r.price).toLocaleString(undefined, { maximumFractionDigits: 2 })
                : '—'}</b>
              <i className={tone(r.change_percent)}>{pctTxt(r.change_percent)}</i>
            </div>
          ))}
        </div>
      )}

      {/* The live panels, unchanged in behaviour and in data; the new look is CSS on `.nal`. */}
      <div className="nal-panels">{panels}</div>

      {/* --------------------------------- options flow, sentiment, sector performance */}
      <div className="nal-row2">
        <section className="nd-card nal-mini">
          <header className="nd-card-head">
            <h3><Layers size={14} /> Options flow</h3>
            <NavLink to="/unusual-activity" className="nal-more">View all →</NavLink>
          </header>
          {flow.data ? (
            <div className="nal-flow">
              <div><span>Call premium</span><b className="up">{money0(flow.data.call_premium)}</b></div>
              <div><span>Put premium</span><b className="down">{money0(flow.data.put_premium)}</b></div>
              <div><span>Call share</span>
                <b>{flow.data.call_premium_share != null
                  ? `${Number(flow.data.call_premium_share).toFixed(0)}%` : '—'}</b></div>
              {!!pc && (
                <div><span>Put/call</span><b>{pc.ratio ?? '—'} <i className="nal-tag">{pc.label || ''}</i></b></div>
              )}
            </div>
          ) : (
            <div className="nd-empty">{flow.loading ? 'Loading…' : 'No flow reading right now.'}</div>
          )}
        </section>

        <section className="nd-card nal-mini">
          <header className="nd-card-head"><h3><Gauge size={14} /> Market sentiment</h3></header>
          {score != null ? (
            <div className="nal-gauge">
              <ResponsiveContainer width="100%" height={132}>
                <PieChart>
                  <Pie data={gauge} dataKey="value" startAngle={180} endAngle={0}
                    innerRadius={46} outerRadius={64} stroke="none" cy="92%">
                    {gauge.map((g) => <Cell key={g.name} fill={g.fill} />)}
                  </Pie>
                  <Tooltip formatter={(v: any, n: any) => (n === 'score' ? [`${v}/100`, 'Score'] : ['', ''])} />
                </PieChart>
              </ResponsiveContainer>
              <div className="nal-gauge-txt">
                <b>{score}<i>/100</i></b>
                <span>{sent?.label || ''}</span>
              </div>
            </div>
          ) : (
            <div className="nd-empty">{pulse.loading ? 'Loading…' : 'No sentiment reading right now.'}</div>
          )}
        </section>

        <section className="nd-card nal-mini">
          <header className="nd-card-head">
            <h3><Target size={14} /> Sector performance</h3>
            <NavLink to="/market" className="nal-more">View all →</NavLink>
          </header>
          {sectors.length ? (
            <ul className="nal-sectors">
              {sectors.map((x: any) => (
                <li key={x.label || x.symbol}>
                  <span>{x.label}</span>
                  <i className={tone(x.change_percent)}>{pctTxt(x.change_percent)}</i>
                </li>
              ))}
            </ul>
          ) : (
            <div className="nd-empty">{pulse.loading ? 'Loading…' : 'No sector reading right now.'}</div>
          )}
        </section>
      </div>

      {ctx.isAdmin && (
        <p className="nal-admin">Landing panels: Top Movers, index prices, earnings and news — the same sources as the classic landing.</p>
      )}
    </div>
  );
}
