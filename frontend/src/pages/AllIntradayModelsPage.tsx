import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, Grid3x3, Search } from 'lucide-react';
import type { PageContext } from '../App';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './all-models.css';

// All Intraday Models -- every intraday board's call per universe name, one sheet.

const tier = (sig?: string) =>
  sig === 'BUY' ? 'b' : sig === 'SELL' ? 's' : sig === 'NEUTRAL' ? 'n' : 'x';
const letter = (sig?: string) =>
  sig === 'BUY' ? 'B' : sig === 'SELL' ? 'S' : sig === 'NEUTRAL' ? '–' : '·';

export default function AllIntradayModelsPage({ ctx }: { ctx: PageContext }) {
  void ctx;
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [changedOnly, setChangedOnly] = useState(false);
  const [sortKey, setSortKey] = useState<string>('symbol');
  const [sortDir, setSortDir] = useState<1 | -1>(1);

  const resp = useApi<any>((s) => api2.allIntradayModelsScan(s), [], { refreshMs: 60_000 });
  const models: [string, string][] = resp.data?.models || [];
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
    if (q) xs = xs.filter((r) => String(r.symbol).toUpperCase().includes(q));
    if (changedOnly) xs = xs.filter((r) => r.changed);
    const cmp = (a: any, b: any) => {
      let av: any; let bv: any;
      if (sortKey === 'symbol') { av = a.symbol; bv = b.symbol; }
      else { av = a.cells?.[sortKey]?.score ?? -1; bv = b.cells?.[sortKey]?.score ?? -1; }
      if (av < bv) return -1 * sortDir;
      if (av > bv) return 1 * sortDir;
      return a.symbol < b.symbol ? -1 : 1;
    };
    return [...xs].sort(cmp);
  }, [rows, query, changedOnly, sortKey, sortDir]);

  const sortBy = (k: string) => {
    if (sortKey === k) setSortDir((d) => (d === 1 ? -1 : 1));
    else { setSortKey(k); setSortDir(k === 'symbol' ? 1 : -1); }
  };

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
        <h1><Grid3x3 size={20} /> All Intraday Models · One Sheet</h1>
        <p>Every intraday board's call for each liquid name, side by side (live 5-min engine +
          equity-signal models). Green = BUY, red = SELL, amber = NEUTRAL. A ringed cell changed
          since the last scan. Click a column header to sort.</p>
      </div>

      <div className="cn-toolbar">
        <span className="cn-search"><Search size={13} />
          <input value={query} placeholder="Filter ticker…" spellCheck={false}
            onChange={(e) => setQuery(e.target.value)} />
        </span>
        <label className="am-check">
          <input type="checkbox" checked={changedOnly} onChange={(e) => setChangedOnly(e.target.checked)} />
          Changed only
        </label>
        <span className="am-legend">
          <i className="b" /> Buy <i className="s" /> Sell <i className="n" /> Neutral
        </span>
        <span className="at-chip">{view.length}</span>
      </div>

      {warming ? (
        <div className="es-empty"><Loader2 size={18} className="spin" /> Scoring every intraday model…</div>
      ) : !view.length ? (
        <div className="es-empty">{query ? `No matches for “${query}”.` : 'No live scores yet — open Trdgo Stock to warm the engine.'}</div>
      ) : (
        <div className="am-wrap">
          <table className="am-tbl">
            <thead>
              <tr>
                <th className="am-sticky am-th-sym" onClick={() => sortBy('symbol')}>Ticker</th>
                <th title="BUY vs SELL across models">Cons.</th>
                {models.map(([k, label]) => (
                  <th key={k} className="am-th-model" onClick={() => sortBy(k)}
                    title="Click to sort by this model's score">{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {view.map((r) => {
                const t = tally(r);
                return (
                  <tr key={r.symbol}>
                    <td className="am-sticky am-sym" onClick={() => navigate(`/claude-pro-intraday/${r.symbol}`)}>
                      <b>{r.symbol}</b>
                      {r.spot != null && <span className="am-co">${Number(r.spot).toFixed(2)}</span>}
                    </td>
                    <td className="am-cons">
                      <span className="am-cons-b">{t.b}</span>
                      <span className="am-cons-s">{t.s}</span>
                    </td>
                    {models.map(([k]) => {
                      const c = r.cells?.[k] || {};
                      return (
                        <td key={k} className={`am-cell ${tier(c.signal)} ${c.changed ? 'chg' : ''}`}
                          title={`${c.signal || 'no data'}${c.score != null ? ` · ${c.score}` : ''}`}>
                          <span className="am-l">{letter(c.signal)}</span>
                          <span className="am-sc">{c.score != null ? c.score : ''}</span>
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
