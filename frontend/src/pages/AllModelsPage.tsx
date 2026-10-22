import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, Grid3x3, Search } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './all-models.css';

// All-in-One sheet: every pre-earnings model's call for each upcoming name, in one
// Excel-style matrix. Rows = stocks reporting soon; columns = models; each cell is
// that model's signal + score, with changed cells highlighted.

const tier = (sig?: string) =>
  sig === 'BUY' ? 'b' : sig === 'SELL' ? 's' : sig === 'NEUTRAL' ? 'n' : sig === 'LOW_DATA' ? 'l' : 'x';
const letter = (sig?: string) =>
  sig === 'BUY' ? 'BUY' : sig === 'SELL' ? 'SELL' : sig === 'NEUTRAL' ? 'NEUTRAL'
    : sig === 'LOW_DATA' ? 'Low data' : 'Pending';

const signedPct = (v: any) =>
  v == null ? null : `${v > 0 ? '+' : ''}${Number(v).toFixed(1)}%`;

const dayLabel = (iso: string) => {
  if (!iso || iso === '￿') return 'TBD';
  try {
    return new Date(iso + 'T12:00:00').toLocaleDateString(undefined,
      { month: 'short', day: '2-digit' });
  } catch { return iso; }
};

export default function AllModelsPage({ ctx }: { ctx: PageContext }) {
  void ctx;
  const navigate = useNavigate();
  const [days, setDays] = useState(15);
  const [query, setQuery] = useState('');
  const [changedOnly, setChangedOnly] = useState(false);
  const [sortKey, setSortKey] = useState<string>('date');
  const [sortDir, setSortDir] = useState<1 | -1>(1);

  const resp = useApi<any>((s) => api2.allModelsScan(days, s), [days], { refreshMs: 120_000 });
  const models: [string, string][] = resp.data?.models || [];
  const modelStats: Record<string, any> = resp.data?.model_stats || {};
  const rows: any[] = resp.data?.rows || [];
  const warming = resp.data?.status === 'WARMING' || (!!resp.data && !rows.length);
  useEffect(() => {
    if (!warming || resp.loading) return undefined;
    const id = window.setTimeout(() => resp.refresh(), 6000);
    return () => window.clearTimeout(id);
  }, [warming, resp.loading, resp.refresh]);

  const view = useMemo(() => {
    const q = query.trim().toUpperCase();
    let xs = rows;
    if (q) xs = xs.filter((r) => String(r.symbol).toUpperCase().includes(q)
      || String(r.company || '').toUpperCase().includes(q));
    if (changedOnly) xs = xs.filter((r) => r.changed);
    const cmp = (a: any, b: any) => {
      let av: any; let bv: any;
      if (sortKey === 'date') { av = a.date || '￿'; bv = b.date || '￿'; }
      else if (sortKey === 'symbol') { av = a.symbol; bv = b.symbol; }
      else { av = a.cells?.[sortKey]?.score ?? -1; bv = b.cells?.[sortKey]?.score ?? -1; }
      if (av < bv) return -1 * sortDir;
      if (av > bv) return 1 * sortDir;
      return a.symbol < b.symbol ? -1 : 1;
    };
    return [...xs].sort(cmp);
  }, [rows, query, changedOnly, sortKey, sortDir]);

  const sortBy = (k: string) => {
    if (sortKey === k) setSortDir((d) => (d === 1 ? -1 : 1));
    else { setSortKey(k); setSortDir(k === 'date' || k === 'symbol' ? 1 : -1); }
  };

  // Consensus tally per row (how many models BUY vs SELL).
  const tally = (r: any) => {
    let b = 0; let s = 0;
    for (const [k] of models) {
      const sig = r.cells?.[k]?.signal;
      if (sig === 'BUY') b++; else if (sig === 'SELL') s++;
    }
    return { b, s };
  };

  return (
    <div className="page am-page">
      <div className="ts-head">
        <h1><Grid3x3 size={20} /> All Models · One Sheet</h1>
        <p>Every earnings model's live call for each upcoming name, read straight from that model's own tab
          (signal · score · data coverage). Green = BUY, red = SELL, amber = NEUTRAL, grey = Low data / Pending.
          A ringed cell changed since the last scan.
          Click a column header to sort; click a ticker for its detail.</p>
      </div>

      <div className="cn-toolbar">
        <label>Window
          <select value={days} onChange={(e) => setDays(+e.target.value)}>
            <option value={7}>7d</option><option value={15}>15d</option><option value={21}>21d</option>
          </select>
        </label>
        <span className="cn-search"><Search size={13} />
          <input value={query} placeholder="Filter ticker / company…" spellCheck={false}
            onChange={(e) => setQuery(e.target.value)} />
        </span>
        <label className="am-check">
          <input type="checkbox" checked={changedOnly} onChange={(e) => setChangedOnly(e.target.checked)} />
          Changed only
        </label>
        <span className="am-legend">
          <i className="b" /> Buy <i className="s" /> Sell <i className="n" /> Neutral <i className="l" /> Low data
        </span>
        <span className="at-chip">{view.length}</span>
      </div>

      {warming ? (
        <div className="es-empty"><Loader2 size={18} className="spin" /> Scoring every model across the calendar…</div>
      ) : !view.length ? (
        <div className="es-empty">{query ? `No matches for “${query}”.` : 'No upcoming reports.'}</div>
      ) : (
        <div className="am-wrap">
          <table className="am-tbl">
            <thead>
              <tr>
                <th className="am-sticky am-th-sym" onClick={() => sortBy('symbol')}>Ticker</th>
                <th onClick={() => sortBy('date')}>Date</th>
                <th title="The company's most recent real post-earnings move">Last move</th>
                <th title="BUY vs SELL across models">Consensus</th>
                <th title="When the oldest scan feeding this row was built (US Eastern)">Last updated (ET)</th>
                {models.map(([k, label]) => {
                  const st = modelStats[k] || {};
                  const acc = st.directional_accuracy;
                  return (
                    <th key={k} className="am-th-model" onClick={() => sortBy(k)}
                      title={`Click to sort by this model's score.\nVerified directional (BUY/SELL) accuracy: `
                        + `${acc != null ? `${acc}% over ${st.directional_n} calls` : 'no history yet'}`}>
                      <span className="am-th-name">{label}</span>
                      <span className={`am-th-acc ${acc == null ? 'none' : acc >= 55 ? 'good' : acc >= 45 ? 'mid' : 'bad'}`}>
                        {acc != null ? `${acc}% · n${st.directional_n}` : 'no hist'}</span>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {view.map((r) => {
                const t = tally(r);
                return (
                  <tr key={r.symbol}>
                    <td className="am-sticky am-sym" onClick={() => navigate(`/claude-last/${r.symbol}`)}>
                      <b>{r.symbol}</b>
                      <span className="am-co">{r.company || ''}</span>
                    </td>
                    <td className="am-date">{dayLabel(r.date)}
                      {r.report_clock ? <em>{r.report_clock}</em> : null}</td>
                    <td className={`am-lastmove ${r.last_move ? (r.last_move.move_pct > 0 ? 'up' : r.last_move.move_pct < 0 ? 'down' : '') : ''}`}
                      title={r.last_move ? `Previous earnings (${r.last_move.earnings_date}): ${signedPct(r.last_move.move_pct)}`
                        : 'No recorded previous earnings move'}>
                      {r.last_move ? signedPct(r.last_move.move_pct) : '—'}</td>
                    <td className="am-cons" title={`${t.b} BUY vs ${t.s} SELL across ${models.length} models`}>
                      <span className="am-cons-b">{r.consensus?.text ?? `${t.b} BUY · ${t.s} SELL`}</span>
                      {(() => {
                        const dirN = t.b + t.s;
                        if (!dirN) return <span className="am-cons-dir n">—</span>;
                        const dir = t.b >= t.s ? 'BUY' : 'SELL';
                        const pct = Math.round(Math.max(t.b, t.s) / dirN * 100);
                        return <span className={`am-cons-dir ${dir === 'BUY' ? 'b' : 's'}`}>{dir} {pct}%</span>;
                      })()}
                    </td>
                    <td className="am-date" title={r.updated_at ? new Date(r.updated_at * 1000).toISOString() : ''}>
                      {r.updated_et || '—'}</td>
                    {models.map(([k]) => {
                      const c = r.cells?.[k] || {};
                      const p = c.prev;
                      return (
                        <td key={k} className={`am-cell ${tier(c.signal)} ${c.changed ? 'chg' : ''}`}
                          title={`${c.signal || 'no data'}${c.score != null ? ` · ${c.score}` : ''}${c.coverage != null ? ` · coverage ${c.coverage}%` : ''}`
                            + (p ? `\nPrevious earnings call: ${p.signal} → actual ${signedPct(p.actual_move_pct)} (${p.result})`
                              : '\nNo recorded previous call')}>
                          <span className="am-l">{letter(c.signal)}</span>
                          <span className="am-sc">{c.score != null ? c.score : ''}</span>
                          {c.coverage != null && <span className="am-cov">{c.coverage}%</span>}
                          {p && <span className={`am-prev ${p.result === 'CORRECT' ? 'ok' : 'no'}`}
                            title={`Previous: ${p.signal} → ${signedPct(p.actual_move_pct)} (${p.result})`}>
                            {p.result === 'CORRECT' ? '✓' : '✗'}</span>}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
