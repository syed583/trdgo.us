/**
 * G — "Which stocks trade in my account", on My Broker.
 *
 * Why the user maps by hand rather than us guessing. Brokers name the same company differently: AAPL,
 * AAPL.m, AAPL.US. C.2 guessed by trying our name and then a list of suffixes against the user's account,
 * and a near-miss suffix can be a different instrument — which is how the operator's own book took
 * wrong-symbol fills (A.18). When the result is a real order in someone's account, guessing is the wrong
 * default. So: search their broker, show what it offers, and let them choose.
 *
 * An unmapped stock is not an error. The signal still appears everywhere on screen; only the order is
 * skipped, and the skip says so with the stock's name.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Loader2, Search, Trash2, Wand2 } from 'lucide-react';
import { api2 } from '../api/client';

type Mapped = { symbol: string; broker_symbol: string; contract_size?: number | null };

export default function MyBrokerSymbols({ onChanged }: { onChanged?: () => void }) {
  const [mapped, setMapped] = useState<Mapped[]>([]);
  const [unmapped, setUnmapped] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [matches, setMatches] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; bad: boolean } | null>(null);
  const [loaded, setLoaded] = useState(false);
  const searchSeq = useRef(0);

  const load = useCallback(async () => {
    try {
      const r = await api2.myMt5Symbols();
      setMapped(r?.mapped || []);
      setUnmapped(r?.unmapped || []);
    } catch (e) {
      setMsg({ text: (e as Error).message, bad: true });
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const search = async (q: string) => {
    const term = q.trim();
    if (!term) { setMatches(null); return; }
    // Each search is numbered: a slow answer for an older term must never overwrite a newer one.
    const seq = ++searchSeq.current;
    setBusy(true); setMsg(null);
    try {
      const r = await api2.myMt5SymbolSearch(term);
      if (seq !== searchSeq.current) return;
      if (r?.ok === false) { setMsg({ text: r.detail || r.reason, bad: true }); setMatches([]); }
      else setMatches(r?.matches || []);
    } catch (e) {
      if (seq === searchSeq.current) setMsg({ text: (e as Error).message, bad: true });
    } finally {
      if (seq === searchSeq.current) setBusy(false);
    }
  };

  const run = async (fn: () => Promise<any>, ok: string) => {
    setBusy(true); setMsg(null);
    try {
      const out = await fn();
      const bad = out?.ok === false;
      setMsg({ text: bad ? (out.detail || out.reason || 'That did not work.') : ok, bad });
      if (!bad) { await load(); onChanged?.(); }
    } catch (e) {
      setMsg({ text: (e as Error).message, bad: true });
    } finally { setBusy(false); }
  };

  const ourSymbol = query.trim().toUpperCase();
  const alreadyMapped = mapped.find((m) => m.symbol === ourSymbol);

  return (
    <div className="mb-card">
      <h3>Which stocks trade in my account</h3>
      <p className="mb-hint">Your broker may spell a stock differently — AAPL, AAPL.m, AAPL.US. Search for a
        stock, pick the name your broker uses, and map it. <b>Only mapped stocks are traded in your
        account.</b> Everything else still appears on screen; the order is simply skipped.</p>

      <div className="mb-shares" style={{ marginBottom: 4 }}>
        <input
          className="mb-custom" style={{ width: 150 }} value={query} placeholder="search e.g. AMD"
          aria-label="search for a stock"
          onChange={(e) => setQuery(e.target.value.toUpperCase().replace(/[^A-Z0-9.\-_]/g, ''))}
          onKeyDown={(e) => { if (e.key === 'Enter') void search(query); }} />
        <button className="mb-btn" disabled={busy || !query.trim()} onClick={() => void search(query)}>
          {busy ? <Loader2 size={13} className="spin" /> : <Search size={13} />} Search my broker
        </button>
        <button className="mb-btn" disabled={busy}
          onClick={() => run(() => api2.myMt5MapAuto(), 'Mapped every stock whose name matches exactly.')}>
          <Wand2 size={13} /> Map all automatic matches
        </button>
      </div>

      {matches !== null && (
        matches.length === 0
          ? <p className="mb-empty">Your broker offers nothing matching “{ourSymbol}”.</p>
          : <table className="mb-table">
            <thead><tr><th>At your broker</th><th>Map to</th><th /></tr></thead>
            <tbody>
              {matches.map((bsym) => (
                <tr key={bsym}>
                  <td>{bsym}</td>
                  <td>{ourSymbol}</td>
                  <td>
                    <button className="mb-btn" disabled={busy}
                      onClick={() => run(() => api2.myMt5MapSymbol(ourSymbol, bsym),
                        `${ourSymbol} will trade as ${bsym} in your account.`)}>
                      <Check size={13} /> {alreadyMapped ? 'Use this instead' : 'Map'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
      )}

      <h4 style={{ margin: '20px 0 0', fontSize: 13 }}>Mapped ({mapped.length})</h4>
      {loaded && mapped.length === 0
        ? <p className="mb-empty">Nothing mapped yet, so no orders will be placed in your account.</p>
        : (
          <table className="mb-table">
            <thead><tr><th>Stock</th><th>At your broker</th><th className="num">Contract size</th><th /></tr></thead>
            <tbody>
              {mapped.map((m) => (
                <tr key={m.symbol}>
                  <td>{m.symbol}</td>
                  <td>{m.broker_symbol}</td>
                  <td className="num">{m.contract_size ?? '—'}</td>
                  <td>
                    <button className="mb-btn danger" disabled={busy}
                      onClick={() => run(() => api2.myMt5UnmapSymbol(m.symbol),
                        `${m.symbol} will no longer be traded in your account.`)}>
                      <Trash2 size={13} /> Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

      {loaded && unmapped.length > 0 && (
        <p className="mb-empty">Not mapped yet ({unmapped.length}): {unmapped.join(', ')}</p>
      )}

      {msg && <p className={`mb-empty${msg.bad ? ' mb-neg' : ''}`}>{msg.text}</p>}
    </div>
  );
}
