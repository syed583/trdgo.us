import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, RefreshCw, Trash2 } from 'lucide-react';
import { api2 } from '../api/client';
import type { PageContext } from '../App';
import { useApi } from '../hooks/useApi';
import { Panel } from '../components/common';
import { money, signedPct, tone } from '../lib/format';
import { ErrorState, Loading, PageHead, StatusChip, Th, useSort } from './shared';

export default function WatchlistPage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const list = useApi<any>((s) => api2.watchlist(s), []);
  const rows: any[] = list.data?.rows || [];
  const { sorted, sort, dir, toggle } = useSort(rows, 'symbol');

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    const symbol = draft.trim().toUpperCase();
    if (!symbol) return;
    setBusy(true);
    setError(null);
    try {
      // Validate against a real IBKR contract before persisting, so the
      // watchlist can never hold a symbol that will not load.
      const check = await api2.validateSymbol(symbol);
      if (!check.valid) {
        setError(check.status === 'SYMBOL_NOT_FOUND'
          ? `SYMBOL NOT FOUND: ${symbol}` : check.detail || 'Could not validate');
        return;
      }
      await api2.watchlistAdd(symbol);
      setDraft('');
      list.refresh();
      ctx.strip.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (symbol: string) => {
    await api2.watchlistRemove(symbol).catch(() => undefined);
    list.refresh();
    ctx.strip.refresh();
  };

  return (
    <div className="page">
      <PageHead
        title="Watchlist"
        subtitle="Persisted in PostgreSQL. The ticker strip on the Earnings screen follows this list."
        right={
          <button className="ghost-btn" onClick={list.refresh}>
            <RefreshCw size={12} className={list.loading ? 'spin' : undefined} />
            Refresh
          </button>
        }
      />

      <Panel title="Add a symbol" noBody>
        <form className="filter-bar" style={{ borderBottom: 0 }} onSubmit={add}>
          <label>
            Ticker
            <input
              type="text" value={draft} placeholder="e.g. MSFT" spellCheck={false}
              onChange={(e) => { setDraft(e.target.value.toUpperCase()); setError(null); }}
            />
          </label>
          <button className="ghost-btn on" type="submit" disabled={busy}>
            <Plus size={11} /> {busy ? 'Checking…' : 'Add'}
          </button>
          {error && <span className="inline-error">{error}</span>}
        </form>
      </Panel>

      <Panel
        title={`Tracked symbols (${rows.length})`}
        noBody
      >
        {list.error ? <ErrorState error={list.error} />
          : list.initialLoading ? <Loading />
            : !rows.length ? (
              <div className="state">
                <span className="state-title">Watchlist is empty</span>
                <span>Add a ticker above, or use the + button on the Scanner.</span>
              </div>
            ) : (
              <div className="tbl-scroll" style={{ maxHeight: 620 }}>
                <table className="tbl">
                  <thead>
                    <tr>
                      <Th label="Ticker" field="symbol" sort={sort} dir={dir} onSort={toggle} />
                      <Th label="Company" field="company" sort={sort} dir={dir} onSort={toggle} />
                      <Th label="Price" field="price" sort={sort} dir={dir} onSort={toggle} align="r" />
                      <Th label="Change" field="change_percent" sort={sort} dir={dir} onSort={toggle} align="r" />
                      <Th label="Next earnings" field="next_earnings" sort={sort} dir={dir} onSort={toggle} />
                      <Th label="Data" />
                      <Th label="" />
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map((r: any) => (
                      <tr key={r.id} className="clickable"
                        onClick={() => navigate(`/earnings/${r.symbol}${ctx.search}`)}>
                        <td><b>{r.symbol}</b></td>
                        <td style={{ color: 'var(--text-dim)' }}>{r.company || '--'}</td>
                        <td className="num r">{money(r.price)}</td>
                        <td className={`num r ${tone(r.change_percent)}`}>
                          {signedPct(r.change_percent)}
                        </td>
                        <td className="num">{r.next_earnings_label || '--'}</td>
                        <td><StatusChip status={r.status} /></td>
                        <td>
                          <button className="mini-btn danger" title="Remove"
                            onClick={(e) => { e.stopPropagation(); remove(r.symbol); }}>
                            <Trash2 size={11} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
      </Panel>
    </div>
  );
}
