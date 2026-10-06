import { useMemo, useState, type CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';
import { CalendarDays, Search, ChevronRight, Bell } from 'lucide-react';
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
        return (
          <div className="ue-notice">
            <Bell size={14} />
            <span className="ue-notice-h">{changed.length} earnings {changed.length === 1
              ? 'stock' : 'stocks'} changed signal</span>
            <span className="ue-notice-list">
              {changed.slice(0, 8).map((s) => (
                <button key={s} className="ue-notice-chip" onClick={() => open(s)}>
                  <b>{s}</b> {prevSig[s]}→{signals[s]}
                </button>
              ))}
              {changed.length > 8 && <span className="ue-notice-more">+{changed.length - 8} more</span>}
            </span>
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
                return (
                <button key={r.symbol + r.date}
                  className={`ue-row sig-${sigClass(r.symbol)} ${style ? 'sig-changed' : ''}`}
                  style={style}
                  onClick={() => open(r.symbol)}
                  title={style
                    ? `Changed: ${prevSig[r.symbol]} → ${signals[r.symbol]}`
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
