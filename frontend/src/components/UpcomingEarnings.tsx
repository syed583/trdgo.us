import { useMemo, useState, type CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';
import { CalendarDays, Search, ChevronRight, Bell, HelpCircle } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './earnings-trade.css';

/** Compact market-cap: $1.2T / $34.5B / $820M. */
function fmtCap(v: number): string {
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${Math.round(v)}`;
}

/**
 * The landing view for the earnings engines: the stocks actually reporting soon.
 * Pick one and it opens that ticker's full analysis on the same engine (`base`).
 */
export default function UpcomingEarnings({
  base, title, demo, search, timeline, hideChanges, profile, mode,
}: {
  base: string; title: string; demo?: boolean; search: string;
  // timeline: show each stock's past-15-day signal track inline on its card.
  timeline?: boolean;
  // hideChanges: no "changed signal" banner and no split/blink on cards (the
  // Earnings Trade landing wants a plain list).
  hideChanges?: boolean;
  // profile: scoring profile for the per-card "why" prefetch (e.g. "final").
  profile?: string;
  // mode: which side colours the cards -- 'stock' (equity call) or 'options'
  // (straddle call). Undefined keeps the original combined (equity) colouring.
  mode?: 'stock' | 'options';
}) {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [showAllChanged, setShowAllChanged] = useState(false);
  const [changeTab, setChangeTab] = useState<'intraday' | 'session'>('intraday');
  const tabsAtTop = false;  // standalone banner only; the Trade page uses `timeline`
  // Lazy "why did it flip?" per changed stock: the current drivers leaning the
  // new way. Snapshots only store the decision, so we explain the new call's
  // live drivers rather than diffing yesterday's parameters.
  const [whyFor, setWhyFor] = useState<string | null>(null);
  const [whyCache, setWhyCache] = useState<Record<string, any>>({});
  // Render only the nearest few days by default; the full 3-week window is 700+
  // cards, which bloats the DOM and the per-card risk fetch. "Show more" reveals
  // the rest on demand.
  const DAYS_STEP = 5;
  const [daysShown, setDaysShown] = useState(DAYS_STEP);
  const toggleWhy = (sym: string) => {
    if (whyFor === sym) { setWhyFor(null); return; }
    setWhyFor(sym);
    if (!whyCache[sym]) {
      setWhyCache((c) => ({ ...c, [sym]: { loading: true } }));
      api2.earningsTrade(sym, profile)
        .then((d) => setWhyCache((c) => ({ ...c, [sym]: d })))
        .catch(() => setWhyCache((c) => ({ ...c, [sym]: { error: true } })));
    }
  };

  const up = useApi<any>(
    (s) => (demo ? Promise.resolve(null) : api2.earningsUpcoming(21, s)),
    [demo],
    { refreshMs: demo ? undefined : 300_000 },
  );
  const rows: any[] = up.data?.rows || [];

  // Latest Buy/Sell/Neutral per listed symbol, to color each card.
  const symKey = rows.map((r) => r.symbol).sort().join(',');
  const sigSource = mode === 'options' ? 'earnings-options' : 'earnings';
  const sig = useApi<any>(
    (s) => (demo || !rows.length ? Promise.resolve(null)
      : api2.signalsLatest(rows.map((r) => r.symbol), sigSource, s)),
    [demo, symKey, sigSource],
    // Poll so cards colour in as the backend warms the missing symbols. 30s
    // (was 15s) halves the background-warm churn while cards still fill in.
    { refreshMs: demo ? undefined : 30_000 },
  );
  const riskOn = mode !== 'options';

  // Pre-earnings signal timeline (only when that tab is active).
  const timelineOn = !!timeline;
  const tl = useApi<any>(
    (s) => (timelineOn && rows.length && !demo
      ? api2.earningsSignalTimeline(rows.map((r) => r.symbol), 15, false, s)
      : Promise.resolve(null)),
    [timelineOn, symKey, demo],
    { refreshMs: timelineOn && !demo ? 60_000 : undefined },
  );

  const signals: Record<string, string> = sig.data?.signals || {};
  // Previous session's call per symbol, present only when it changed.
  const prevSig: Record<string, string> = sig.data?.prev || {};
  // How it changed: 'intraday' (moved since today's capture) vs 'session'
  // (day-over-day), and the date the current call is compared against.
  const changeKind: Record<string, string> = sig.data?.change_kind || {};
  const changeSince: Record<string, string> = sig.data?.change_since || {};

  // The changed symbols, split by kind -- used by the standalone banner.
  const changed = rows.map((r) => r.symbol)
    .filter((s) => prevSig[s] && signals[s] && prevSig[s] !== signals[s]);
  const intradayChanged = changed.filter((s) => changeKind[s] === 'intraday');
  const sessionChanged = changed.filter((s) => changeKind[s] !== 'intraday');
  const sinceLabel = (iso?: string) => {
    if (!iso) return '';
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  };
  const CLS: any = { BUY: 'buy', SELL: 'sell', NEUTRAL: 'neutral' };
  const sigClass = (sym: string) => CLS[signals[sym]] || 'none';
  // Card tint by the earnings score tier. The colour must agree with the trade
  // text the card shows, so it is driven by the SAME source as that text -- the
  // risk batch (`riskMap`, from earn:trade:default). A card with no confidence
  // reads "No clear trade", so it is neutral, never green/red. The STRONG shade
  // comes from the raw equity decision (`tiers`). Before the risk batch has an
  // entry, fall back to the raw tier, then the normalised signal.
  // Only three colours, strictly by the 0-100 score: BUY (>=55) green,
  // SELL (<=45) red, the neutral middle yellow. Before the score is
  // known, fall back to the raw decision / signal.
  const tiers: Record<string, string> = sig.data?.tiers || {};
  const scores: Record<string, number> = sig.data?.scores || {};
  const tierClass = (sym: string) => {
    const sc = scores[sym];
    if (sc != null) return sc >= 55 ? 'buy' : sc <= 45 ? 'sell' : 'neutral';
    const raw = (tiers[sym] || riskMap[sym]?.decision || '').toUpperCase();
    if (!raw) return sigClass(sym);                        // not warmed yet
    if (raw.includes('SELL')) return 'sell';
    if (raw.includes('BUY')) return 'buy';
    return 'neutral';
  };
  // RGB of each signal colour, for the half/half gradient on a changed card.
  const RGB: Record<string, string> = {
    buy: '46,184,122', sell: '242,70,90', neutral: '217,164,65',
  };
  // Split style when the call changed from a previous session (e.g. Buy->Neutral):
  // one half the old colour, the other half the new.
  const splitFor = (sym: string): CSSProperties | undefined => {
    const cur = sigClass(sym);
    const prv = CLS[prevSig[sym]];
    if (!prv || prv === cur || cur === 'none') return undefined;
    return {
      // Hard-edged half/half so the flip is unmistakable: left half the OLD
      // call's colour, right half the NEW, with a crisp divider between them.
      background: `linear-gradient(100deg,`
        + ` rgba(${RGB[prv]},.42) 0%, rgba(${RGB[prv]},.42) 48%,`
        + ` rgba(255,255,255,.55) 49.5%, rgba(255,255,255,.55) 50.5%,`
        + ` rgba(${RGB[cur]},.42) 52%, rgba(${RGB[cur]},.42) 100%)`,
      borderLeftColor: `rgb(${RGB[prv]})`,
    };
  };

  const groups = useMemo(() => {
    const needle = q.trim().toUpperCase();
    const filtered = needle
      ? rows.filter((r) => (r.symbol || '').toUpperCase().includes(needle)
        || (r.company || '').toUpperCase().includes(needle))
      : rows;
    const byDay: Record<string, any[]> = {};
    for (const r of filtered) {
      const k = r.date_label || r.date || '—';
      (byDay[k] = byDay[k] || []).push(r);
    }
    return Object.entries(byDay);
  }, [rows, q]);

  // While searching, show every match; otherwise cap to the nearest N days.
  const shownGroups = q.trim() ? groups : groups.slice(0, daysShown);
  const moreDays = q.trim() ? 0 : Math.max(0, groups.length - daysShown);

  // Per-card Confidence + Exit (Stock tab only), fetched ONLY for the days
  // actually on screen -- not the whole 3-week universe -- so the list stays
  // light. Served from cache and warmed in the background.
  const visSyms = shownGroups.flatMap(([, items]) => items.map((r: any) => r.symbol));
  const visKey = visSyms.slice().sort().join(',');
  const risk = useApi<any>(
    (s) => (demo || !visSyms.length || !riskOn ? Promise.resolve(null)
      : api2.earningsRiskBatch(visSyms, s)),
    [demo, visKey, riskOn],
    { refreshMs: demo || !riskOn ? undefined : 60_000 },
  );
  const riskMap: Record<string, any> = risk.data?.data || {};

  const open = (sym: string) => navigate(`${base}/${sym}${search}`);

  // The "why did it flip?" panel for the currently-expanded changed stock.
  const renderWhyPanel = () => {
    if (!whyFor) return null;
    const d = whyCache[whyFor];
    const cur = signals[whyFor];
    const want = cur === 'BUY' ? 'Bullish' : cur === 'SELL' ? 'Bearish' : null;
    const why: any[] = (d?.equity?.why || []).filter((p: any) => p.available);
    const drivers = (want ? why.filter((p) => p.leaning === want) : why).slice(0, 4);
    const when = changeKind[whyFor] === 'intraday'
      ? 'moved intraday' : `changed since ${sinceLabel(changeSince[whyFor])}`;
    const score = d?.equity?.score;
    const cov = d?.equity?.coverage;
    const forN = why.filter((p: any) => p.leaning === want).length;
    const againstN = why.filter((p: any) => p.leaning
      === (want === 'Bullish' ? 'Bearish' : 'Bullish')).length;
    const moves: any[] = (d?.historical_moves || [])
      .filter((m: any) => typeof m?.move_pct === 'number');
    const ups = moves.filter((m) => m.move_pct > 0);
    const downs = moves.filter((m) => m.move_pct < 0);
    const avg = (a: any[]) => a.length
      ? a.reduce((s, m) => s + m.move_pct, 0) / a.length : null;
    const aligned = cur === 'BUY' ? ups.length : cur === 'SELL' ? downs.length : null;
    const pct1 = (v: number | null) => v == null ? '--' : `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
    return (
      <div className="ue-why">
        <div className="ue-why-h">
          <b>{whyFor}</b> {prevSig[whyFor]}→<b>{cur}</b> ({when}) —{' '}
          {want ? `what's pushing it ${cur.toLowerCase()} now:` : 'no decisive edge now:'}
        </div>
        {!d?.loading && !d?.error && (
          <div className="ue-why-stats">
            <div className="ue-why-stat">
              <span>Conviction</span>
              <b>{score != null ? `${Math.round(score)}/100` : '--'}</b>
              <em>{cov != null ? `${Math.round(cov)}% coverage` : 'coverage n/a'}
                {want ? ` · ${forN} for / ${againstN} against` : ''}</em>
            </div>
            <div className="ue-why-stat">
              <span>Past earnings ({moves.length})</span>
              <b>{aligned != null && moves.length
                ? `${aligned}/${moves.length} ${cur === 'BUY' ? 'up' : 'down'}`
                : moves.length ? `${ups.length}↑ / ${downs.length}↓` : '--'}</b>
              <em>{moves.length
                ? `avg ${pct1(avg(ups))} up · ${pct1(avg(downs))} down`
                : 'no history'}</em>
            </div>
          </div>
        )}
        {d?.loading ? (
          <div className="ue-why-note">Loading the drivers…</div>
        ) : d?.error ? (
          <div className="ue-why-note">Couldn't load the reasons.</div>
        ) : !drivers.length ? (
          <div className="ue-why-note">
            No single dominant driver — it's the balance of the full
            scorecard. Open {whyFor} for the complete breakdown.
          </div>
        ) : (
          <ul className="ue-why-list">
            {drivers.map((p, i) => (
              <li key={i}>
                <span className={`ue-why-dot ${p.leaning === 'Bullish' ? 'pos'
                  : p.leaning === 'Bearish' ? 'neg' : 'neu'}`} />
                <span className="ue-why-label">{p.label}</span>
                <span className="ue-why-pts">{p.points_label}</span>
                {p.detail && <span className="ue-why-detail">{p.detail}</span>}
              </li>
            ))}
          </ul>
        )}
        <button className="ue-why-open" onClick={() => open(whyFor!)}>
          See full breakdown →
        </button>
      </div>
    );
  };

  // The past-15-day Stk/Opt signal track for one stock, rendered as a strip
  // beneath its card. Built from the daily snapshots (grey = no capture).
  const tlCols: string[] = tl.data?.cols || [];
  const tlRowsData: Record<string, any> = tl.data?.rows || {};
  const renderStrip = (sym: string) => {
    const row = tlRowsData[sym];
    if (!timeline || !row || !tlCols.length) return null;
    const cls = (v: any) => `tl-cell sig-${CLS[v] || 'na'}`;
    return (
      <div className="tl-strip">
        <span className="tl-line"><span className="tl-lbl">Stk</span>
          {row.stock.map((v: any, i: number) => (
            <i key={i} className={cls(v)} title={`${tlCols[i]}: ${v || '—'}`} />))}
        </span>
        <span className="tl-line"><span className="tl-lbl">Opt</span>
          {row.options.map((v: any, i: number) => (
            <i key={i} className={cls(v)} title={`${tlCols[i]}: ${v || '—'}`} />))}
        </span>
      </div>
    );
  };

  return (
    <div className="ue">
      <div className="ue-head">
        <div className="ue-title"><CalendarDays size={16} /> {title}</div>
        <div className="ue-search">
          <Search size={14} />
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Filter ticker or company…" />
        </div>
      </div>
      <p className="ue-sub">Companies reporting in the next ~3 weeks. Pick one to
        score its earnings trade.</p>

      {/* Colour convention for the cards, by the 0-100 earnings score. */}
      <div className="ue-legend">
        <span className="ue-leg buy"><i />Buy ≥ 65 (green)</span>
        <span className="ue-leg neu"><i />Exit / No trade 35–65 (yellow)</span>
        <span className="ue-leg sell"><i />Sell ≤ 35 (red)</span>
      </div>

      {/* Plain-list mode: a notice listing which stocks changed (no Intraday
          tabs) + the why panel. */}
      {hideChanges && changed.length > 0 && (() => {
        const shown = showAllChanged ? changed : changed.slice(0, 12);
        return (
          <div className="ue-chgnote">
            <div className="ue-chgnote-top">
              <Bell size={13} />
              <span className="ue-chgnote-h">{changed.length} earnings {changed.length === 1
                ? 'stock' : 'stocks'} changed signal</span>
            </div>
            <div className="ue-chgnote-list">
              {shown.map((s) => (
                <span key={s} className="ue-notice-chipwrap">
                  <button className="ue-notice-chip" onClick={() => open(s)}>
                    <b>{s}</b> {prevSig[s]}→{signals[s]}
                  </button>
                  <button type="button" className={`ue-why-btn${whyFor === s ? ' on' : ''}`}
                    title="Why did this change?" onClick={() => toggleWhy(s)}>
                    why?
                  </button>
                </span>
              ))}
              {changed.length > 12 && (
                <button type="button" className="ue-notice-toggle"
                  onClick={() => setShowAllChanged((v) => !v)}>
                  {showAllChanged ? 'Show less' : `+${changed.length - 12} more`}
                </button>
              )}
            </div>
          </div>
        );
      })()}
      {hideChanges && renderWhyPanel()}

      {(() => {
        // Standalone banner (equity/options landings). Suppressed on the Trade
        // page (timeline strips, or a plain list via hideChanges).
        if (timeline || hideChanges || !changed.length) return null;
        const intraday = intradayChanged;
        const session = sessionChanged;

        // One chip + its "why?" affordance.
        const chip = (s: string) => (
          <span key={s} className="ue-notice-chipwrap">
            <button className="ue-notice-chip" onClick={() => open(s)}>
              <b>{s}</b> {prevSig[s]}→{signals[s]}
            </button>
            <button type="button" className={`ue-why-btn${whyFor === s ? ' on' : ''}`}
              title="Why did this change?" onClick={() => toggleWhy(s)}>
              <HelpCircle size={13} /> why?
            </button>
          </span>
        );

        // Controlled: respect the parent's tab exactly. Standalone: default to
        // whichever kind actually has changes.
        const active = tabsAtTop ? changeTab
          : (changeTab === 'session'
            ? (session.length ? 'session' : 'intraday')
            : (intraday.length ? 'intraday' : 'session'));
        const list = active === 'intraday' ? intraday : session;
        const LIMIT = 8;
        const shown = showAllChanged ? list : list.slice(0, LIMIT);
        const hidden = list.length - shown.length;

        return (
          <div className="ue-notice">
            {changed.length > 0 && (
              <div className="ue-notice-top">
                <Bell size={14} />
                <span className="ue-notice-h">{changed.length} earnings {changed.length === 1
                  ? 'stock' : 'stocks'} changed signal</span>
              </div>
            )}
            {!tabsAtTop && (
              <div className="ue-chg-tabs" role="tablist">
                <button type="button" role="tab"
                  className={`ue-chg-tab intraday${active === 'intraday' ? ' on' : ''}`}
                  onClick={() => { setChangeTab('intraday'); setShowAllChanged(false); }}>
                  Intraday · moved today <span className="ue-chg-count">{intraday.length}</span>
                </button>
                <button type="button" role="tab"
                  className={`ue-chg-tab session${active === 'session' ? ' on' : ''}`}
                  onClick={() => { setChangeTab('session'); setShowAllChanged(false); }}>
                  Last 10 days <span className="ue-chg-count">{session.length}</span>
                </button>
              </div>
            )}
            <div className="ue-chg-panel">
              {!list.length ? (
                <span className="ue-why-note">
                  {active === 'intraday'
                    ? 'No calls have moved intraday since today’s capture.'
                    : 'No day-over-day changes in the last 10 days.'}
                </span>
              ) : (
                <span className="ue-notice-list">
                  {shown.map(chip)}
                  {list.length > LIMIT && (
                    <button type="button" className="ue-notice-toggle"
                      onClick={() => setShowAllChanged((v) => !v)}>
                      {showAllChanged ? 'Show less' : `+${hidden} more`}
                    </button>
                  )}
                </span>
              )}
            </div>
            {renderWhyPanel()}
          </div>
        );
      })()}

      {/* Timeline mode: the "why?" panel renders standalone above the list. */}
      {timeline && renderWhyPanel()}

      {demo ? (
        <div className="es-empty">Disabled in demo mode.</div>
      ) : up.initialLoading ? (
        <div className="es-empty">Loading the earnings calendar…</div>
      ) : !rows.length ? (
        <div className="es-empty">No scheduled reports in the window.</div>
      ) : !groups.length ? (
        <div className="es-empty">
          {q ? `No matches for “${q}”.` : 'No scheduled reports in the window.'}
        </div>
      ) : (
        shownGroups.map(([day, items]) => (
          <div key={day} className="ue-day">
            <div className="ue-day-h">{day}</div>
            <div className={`ue-grid${timeline ? ' ue-grid-timeline' : ''}`}>
              {items.map((r) => {
                // Change status (half/half split + pink blink) shows on cards even
                // when the banner is hidden -- hideChanges only hides the banner.
                // Every changed call blinks pink.
                const style = splitFor(r.symbol);
                const chg = style ? 'sig-changed sig-changed-intraday' : '';
                return (
                <div key={r.symbol + r.date} className={`ue-cardwrap${style ? ' cw-changed' : ''}`}>
                <button
                  className={`ue-row sig-${tierClass(r.symbol)} ${chg}`}
                  onClick={() => open(r.symbol)}
                  title={style
                    ? `${changeKind[r.symbol] === 'intraday' ? 'Intraday' : 'Since ' + sinceLabel(changeSince[r.symbol])}: ${prevSig[r.symbol]} → ${signals[r.symbol]}`
                    : (signals[r.symbol] ? `Signal: ${signals[r.symbol]}` : undefined)}>
                  <div className="ue-row-l">
                    <span className="ue-sym">{r.symbol}</span>
                    <span className="ue-co">{r.company || ''}</span>
                  </div>
                  <div className="ue-row-r">
                    {(r.prior_close != null || r.market_cap != null) && (
                      <span className="ue-quote">
                        {r.prior_close != null && <b>${Number(r.prior_close).toFixed(2)}</b>}
                        {r.market_cap != null && <em>{fmtCap(Number(r.market_cap))}</em>}
                      </span>
                    )}
                    {style && (
                      <span className="ue-row-why" role="button" tabIndex={0}
                        title="Why did this change?"
                        onClick={(e) => { e.stopPropagation(); toggleWhy(r.symbol); }}>
                        why?
                      </span>
                    )}
                    {r.sector && <span className="ue-sector">{r.sector}</span>}
                    {r.short_label && <span className="ue-timing">{r.short_label}</span>}
                    <ChevronRight size={15} className="ue-chev" />
                  </div>
                </button>
                {riskOn && (() => {
                  const rk = riskMap[r.symbol];
                  if (!rk) return <div className="ue-risk ue-risk-wait">Checking the trade…</div>;
                  if (rk.confidence == null) return (
                    <div className="ue-risk ue-risk-none">
                      <span className="ue-none-h">No clear trade — skip this one</span>
                      {rk.reason && <span className="ue-none-why">{rk.reason}</span>}
                    </div>
                  );
                  const ACTION: Record<string, string> = {
                    green: 'Good trade — full size',
                    yellow: 'OK — take half size',
                    red: 'Risky — hedge it or skip',
                  };
                  return (
                    <div className="ue-risk">
                      <div className={`ue-act tone-${rk.conf_color}`}>
                        <span className="ue-act-dot" />
                        {ACTION[rk.conf_color] || 'Review'}
                        <span className="ue-act-conf">Confidence {Math.round(rk.confidence)}/100</span>
                      </div>
                      {(rk.target != null || rk.stop != null) && (
                        <div className="ue-plan">
                          <span className="pos">Aim ${rk.target ?? '—'}</span>
                          <span className="sep">·</span>
                          <span className="neg">Exit if it hits ${rk.stop ?? '—'}</span>
                        </div>
                      )}
                    </div>
                  );
                })()}
                {renderStrip(r.symbol)}
                </div>
                );
              })}
            </div>
          </div>
        ))
      )}

      {moreDays > 0 && (
        <button type="button" className="ue-showmore"
          onClick={() => setDaysShown((n) => n + DAYS_STEP)}>
          Show more days ({moreDays} more)
        </button>
      )}
    </div>
  );
}
