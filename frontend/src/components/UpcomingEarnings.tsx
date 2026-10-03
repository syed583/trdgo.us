import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CalendarDays, Search, ChevronRight } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './earnings-trade.css';

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
  const sigClass = (sym: string) =>
    ({ BUY: 'buy', SELL: 'sell', NEUTRAL: 'neutral' } as any)[signals[sym]] || 'none';

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
              {items.map((r) => (
                <button key={r.symbol + r.date} className={`ue-row sig-${sigClass(r.symbol)}`}
                  onClick={() => open(r.symbol)}
                  title={signals[r.symbol] ? `Signal: ${signals[r.symbol]}` : undefined}>
                  <div className="ue-row-l">
                    <span className="ue-sym">{r.symbol}</span>
                    <span className="ue-co">{r.company || ''}</span>
                  </div>
                  <div className="ue-row-r">
                    {r.sector && <span className="ue-sector">{r.sector}</span>}
                    {r.short_label && <span className="ue-timing">{r.short_label}</span>}
                    <ChevronRight size={15} className="ue-chev" />
                  </div>
                </button>
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
