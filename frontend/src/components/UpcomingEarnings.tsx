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
  base, title, demo, search,
}: { base: string; title: string; demo?: boolean; search: string }) {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [showAllChanged, setShowAllChanged] = useState(false);
  // Lazy "why did it flip?" per changed stock: the current drivers leaning the
  // new way. Snapshots only store the decision, so we explain the new call's
  // live drivers rather than diffing yesterday's parameters.
  const [whyFor, setWhyFor] = useState<string | null>(null);
  const [whyCache, setWhyCache] = useState<Record<string, any>>({});
  const toggleWhy = (sym: string) => {
    if (whyFor === sym) { setWhyFor(null); return; }
    setWhyFor(sym);
    if (!whyCache[sym]) {
      setWhyCache((c) => ({ ...c, [sym]: { loading: true } }));
      api2.earningsTrade(sym)
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
  const sig = useApi<any>(
    (s) => (demo || !rows.length ? Promise.resolve(null)
      : api2.signalsLatest(rows.map((r) => r.symbol), 'earnings', s)),
    [demo, symKey],
    // Poll so cards colour in as the backend captures the missing symbols.
    { refreshMs: demo ? undefined : 15_000 },
  );
  const signals: Record<string, string> = sig.data?.signals || {};
  // Previous session's call per symbol, present only when it changed.
  const prevSig: Record<string, string> = sig.data?.prev || {};
  // How it changed: 'intraday' (moved since today's capture) vs 'session'
  // (day-over-day), and the date the current call is compared against.
  const changeKind: Record<string, string> = sig.data?.change_kind || {};
  const changeSince: Record<string, string> = sig.data?.change_since || {};
  const sinceLabel = (iso?: string) => {
    if (!iso) return '';
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  };
  const CLS: any = { BUY: 'buy', SELL: 'sell', NEUTRAL: 'neutral' };
  const sigClass = (sym: string) => CLS[signals[sym]] || 'none';
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

  const open = (sym: string) => navigate(`${base}/${sym}${search}`);

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

      {(() => {
        const changed = rows
          .map((r) => r.symbol)
          .filter((s) => prevSig[s] && signals[s] && prevSig[s] !== signals[s]);
        if (!changed.length) return null;
        const intraday = changed.filter((s) => changeKind[s] === 'intraday');
        const session = changed.filter((s) => changeKind[s] !== 'intraday');

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

        // A titled group of changed chips (collapsed to 8 unless expanded).
        const LIMIT = 8;
        const groupBlock = (list: string[], kind: 'intraday' | 'session') => {
          if (!list.length) return null;
          const shown = showAllChanged ? list : list.slice(0, LIMIT);
          const hidden = list.length - shown.length;
          return (
            <div className={`ue-chg-group ${kind}`}>
              <span className="ue-chg-tag">
                {kind === 'intraday'
                  ? `Intraday · moved today (${list.length})`
                  : `Since last session (${list.length})`}
              </span>
              <span className="ue-notice-list">
                {shown.map(chip)}
                {list.length > LIMIT && (
                  <button type="button" className="ue-notice-toggle"
                    onClick={() => setShowAllChanged((v) => !v)}>
                    {showAllChanged ? 'Show less' : `+${hidden} more`}
                  </button>
                )}
              </span>
            </div>
          );
        };

        return (
          <div className="ue-notice">
            <div className="ue-notice-top">
              <Bell size={14} />
              <span className="ue-notice-h">{changed.length} earnings {changed.length === 1
                ? 'stock' : 'stocks'} changed signal</span>
            </div>
            {groupBlock(intraday, 'intraday')}
            {groupBlock(session, 'session')}
            {whyFor && (() => {
              const d = whyCache[whyFor];
              const cur = signals[whyFor];
              const want = cur === 'BUY' ? 'Bullish' : cur === 'SELL' ? 'Bearish' : null;
              const why: any[] = (d?.equity?.why || [])
                .filter((p: any) => p.available);
              const drivers = (want ? why.filter((p) => p.leaning === want) : why).slice(0, 4);
              const when = changeKind[whyFor] === 'intraday'
                ? 'moved intraday' : `changed since ${sinceLabel(changeSince[whyFor])}`;
              return (
                <div className="ue-why">
                  <div className="ue-why-h">
                    <b>{whyFor}</b> {prevSig[whyFor]}→<b>{cur}</b> ({when}) —{' '}
                    {want ? `what's pushing it ${cur.toLowerCase()} now:` : 'no decisive edge now:'}
                  </div>
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
            })()}
          </div>
        );
      })()}

      {demo ? (
        <div className="es-empty">Disabled in demo mode.</div>
      ) : up.initialLoading ? (
        <div className="es-empty">Loading the earnings calendar…</div>
      ) : !rows.length ? (
        <div className="es-empty">No scheduled reports in the window.</div>
      ) : !groups.length ? (
        <div className="es-empty">No matches for “{q}”.</div>
      ) : (
        groups.map(([day, items]) => (
          <div key={day} className="ue-day">
            <div className="ue-day-h">{day}</div>
            <div className="ue-grid">
              {items.map((r) => {
                const style = splitFor(r.symbol);
                // Intraday flips blink (fresh, moved today); day-over-day flips
                // show the split with a calm steady glow, no blink.
                const chg = style
                  ? (changeKind[r.symbol] === 'intraday'
                    ? 'sig-changed sig-changed-intraday'
                    : 'sig-changed sig-changed-session')
                  : '';
                return (
                <button key={r.symbol + r.date}
                  className={`ue-row sig-${sigClass(r.symbol)} ${chg}`}
                  style={style}
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
                    {r.sector && <span className="ue-sector">{r.sector}</span>}
                    {r.short_label && <span className="ue-timing">{r.short_label}</span>}
                    <ChevronRight size={15} className="ue-chev" />
                  </div>
                </button>
                );
              })}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
