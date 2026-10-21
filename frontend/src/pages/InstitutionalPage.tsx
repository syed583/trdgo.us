import { useState } from 'react';
import { Building2, Loader2 } from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import './model-accuracy.css';
import './institutional.css';

/**
 * Institutional Intelligence (SEC 13F) — read-only, informational.
 *
 * Completely separate from the Earnings-AI BUY/SELL engine: its
 * BULLISH/BEARISH/NEUTRAL labels describe quarterly 13F ownership change only,
 * are styled in a distinct teal/violet/slate palette, and never feed a trading
 * score. Data is delayed quarterly disclosure, not live order flow.
 */

const fmt = (n: number | null | undefined) =>
  n == null ? '—' : Number(n).toLocaleString();
const sign = (n: number | null | undefined) =>
  n == null ? '—' : `${n > 0 ? '+' : ''}${Number(n).toLocaleString()}`;
const pct = (n: number | null | undefined) =>
  n == null ? '—' : `${n > 0 ? '+' : ''}${n}%`;

const TABS: [string, string][] = [
  ['overview', 'Overview'], ['stock', 'Stock Analyzer'], ['rankings', 'Activity Rankings'],
  ['managers', 'Manager Explorer'], ['history', 'Ownership History'], ['mapping', 'CUSIP Mapping'],
];

const RANK_KINDS: [string, string][] = [
  ['accumulation', 'Top accumulation'], ['distribution', 'Top distribution'],
  ['largest_increase', 'Largest share increases'], ['largest_decrease', 'Largest share decreases'],
  ['most_new', 'Most new positions'], ['most_closed', 'Most closed positions'],
  ['strongest_change', 'Strongest change'],
];

const Pill = ({ cls }: { cls: string }) =>
  <span className={`inst-pill ${cls}`}>{(cls || 'na').toUpperCase()}</span>;

const Unavailable = ({ d }: { d: any }) => (
  <div className="ma-note">
    {d?.detail || 'The Institutional Intelligence development dataset is not configured in this environment.'}
  </div>
);

function Disclosure() {
  return (
    <div className="inst-disc">
      ⚠ SEC Form 13F is <b>delayed quarterly disclosure</b> (filed up to 45 days after quarter-end) —
      <b> not live order flow</b>. Institutional <b>BULLISH / BEARISH / NEUTRAL</b> labels describe
      ownership change only and are <b>separate from the Trdgo BUY / SELL trading signals</b>.
    </div>
  );
}

/* ---------------------------------------------------------------- Overview */
function Overview() {
  const q = useApi<any>((s) => api2.instOverview(s), [], { refreshMs: 300_000 });
  const d = q.data;
  if (q.initialLoading) return <div className="es-empty"><Loader2 size={18} className="spin" /> Loading…</div>;
  if (!d || d.status === 'UNAVAILABLE') return <Unavailable d={d} />;
  const m = d.meta || {};
  return (
    <>
      <div className="ma-cards">
        {[['Managers', fmt(d.total_managers)], ['Reported holdings', fmt(d.total_reported_holdings)],
          ['Securities', fmt(d.securities_tracked)], ['Latest quarter', d.latest_quarter],
          ['New positions', fmt(d.new_positions)], ['Increased', fmt(d.increased_positions)],
          ['Reduced', fmt(d.reduced_positions)], ['Closed', fmt(d.closed_positions)],
          ['Unchanged', fmt(d.unchanged_positions)], ['Net share change', sign(d.total_reported_share_change)]]
          .map(([k, v]) => (
            <div className="ma-card" key={k as string}><div className="k">{k}</div><div className="v">{v}</div></div>
          ))}
      </div>
      <div className="inst-note">
        Comparison {d.comparison}. Change counts are per manager + CUSIP.
        Filings {m.filing_date_range?.[0]} … {m.filing_date_range?.[1]} ({m.filing_date_basis}).
      </div>
    </>
  );
}

/* ----------------------------------------------------------- Stock Analyzer */
function StockAnalyzer() {
  const [tickIn, setTickIn] = useState('AAPL');
  const [asOfIn, setAsOfIn] = useState('');
  const [tick, setTick] = useState('AAPL');
  const [asOf, setAsOf] = useState('');
  const q = useApi<any>((s) => api2.instStock(tick, asOf, s), [tick, asOf], { refreshMs: 300_000 });
  const d = q.data;
  const go = () => { setTick(tickIn.trim().toUpperCase()); setAsOf(asOfIn.trim()); };
  return (
    <>
      <div className="inst-row">
        <input value={tickIn} onChange={(e) => setTickIn(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && go()} placeholder="Ticker (e.g. AAPL)" />
        <input value={asOfIn} onChange={(e) => setAsOfIn(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && go()} placeholder="as-of date (optional, YYYY-MM-DD)" />
        <button onClick={go}>Analyze</button>
      </div>
      {q.initialLoading ? <div className="es-empty"><Loader2 size={18} className="spin" /> Loading…</div>
        : !d || d.status === 'UNAVAILABLE' ? <Unavailable d={d} />
        : d.status === 'UNKNOWN_TICKER' ? <div className="ma-note">No institutional mapping for “{d.ticker}”.</div>
        : (
          <>
            <div className="ma-h" style={{ margin: '4px 0 10px' }}>
              {d.company_name}{' '}
              <span className={`inst-conf ${d.mapping_confidence}`}>{d.mapping_confidence}</span>{' '}
              <span className="ma-n">{d.ticker} · {d.cusip} · {d.security_type || ''} {d.exchange || ''}</span>
            </div>
            {d.note && <div className="ma-note" style={{ marginBottom: 10 }}>{d.note}</div>}
            <div className="ma-cards">
              {[['Holders', fmt(d.institutional_holders)], ['Reported shares', fmt(d.total_reported_shares)],
                ['Prev quarter', fmt(d.previous_quarter_shares)], ['Net change', sign(d.net_share_change)],
                ['% change', pct(d.pct_change)]].map(([k, v]) => (
                  <div className="ma-card" key={k as string}><div className="k">{k}</div><div className="v">{v}</div></div>
                ))}
            </div>
            <div className="ma-cards">
              {[['New', d.new_positions], ['Increased', d.increased_positions], ['Reduced', d.reduced_positions],
                ['Closed', d.closed_positions], ['Unchanged', d.unchanged_positions]].map(([k, v]) => (
                  <div className="ma-card" key={k as string}><div className="k">{k}</div><div className="v">{fmt(v as number)}</div></div>
                ))}
            </div>
            <div className="inst-row">
              <span>Institutional classification: <Pill cls={d.institutional_classification} /></span>
              <span className="ma-n">{d.classification_basis}</span>
            </div>
            <div className="inst-note">
              Net-share direction:{' '}
              <span className={d.net_share_direction === 'up' ? 'inst-dir-up' : d.net_share_direction === 'down' ? 'inst-dir-down' : ''}>
                <b>{d.net_share_direction}</b></span>{' '}
              (can differ from the fund-count classification).
              {d.comparison_quarters && <> Comparing {d.comparison_quarters}.</>}
              {d.effective_quarter && !d.comparison_quarters && <> Showing {d.effective_quarter}.</>}
              {' '}Latest filing {d.latest_filing_date || '—'}.
              {d.verifiable === false && <> <b>Not verifiable as-of this date.</b></>}
            </div>
            <div className="inst-note">{d.historical_identity_note} {d.amendment_handling}</div>
          </>
        )}
    </>
  );
}

/* --------------------------------------------------------------- Rankings */
function Rankings() {
  const [kind, setKind] = useState('accumulation');
  const q = useApi<any>((s) => api2.instRankings(kind, 25, 0, s), [kind], { refreshMs: 300_000 });
  const d = q.data;
  return (
    <>
      <div className="inst-row">
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {RANK_KINDS.map(([k, l]) => <option value={k} key={k}>{l}</option>)}
        </select>
        <span className="ma-n">Fund-count direction is shown separately from net-share direction.</span>
      </div>
      {q.initialLoading ? <div className="es-empty"><Loader2 size={18} className="spin" /> Loading…</div>
        : !d || d.status === 'UNAVAILABLE' ? <Unavailable d={d} />
        : (
          <div style={{ overflowX: 'auto' }}>
            <table className="ma-tbl">
              <thead><tr>
                <th className="l">Ticker</th><th className="l">Company</th><th>Q1 sh</th><th>Q2 sh</th>
                <th>Net</th><th>%</th><th>New</th><th>Inc</th><th>Red</th><th>Closed</th><th>Holders</th>
              </tr></thead>
              <tbody>
                {(d.rows || []).map((r: any) => (
                  <tr key={r.cusip}>
                    <td className="l"><b>{r.ticker || '—'}</b></td>
                    <td className="l">{r.company_name}</td>
                    <td>{fmt(r.q1_shares)}</td><td>{fmt(r.q2_shares)}</td>
                    <td className={r.net_share_change > 0 ? 'inst-dir-up' : 'inst-dir-down'}>{sign(r.net_share_change)}</td>
                    <td>{pct(r.pct_change)}</td><td>{r.new_positions}</td><td>{r.increased}</td>
                    <td>{r.reduced}</td><td>{r.closed}</td><td>{r.holders}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </>
  );
}

/* --------------------------------------------------------------- Managers */
function Managers() {
  const [qIn, setQIn] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const limit = 25;
  const q = useApi<any>((s) => api2.instManagers(query, '2026-Q2', limit, page * limit, s),
    [query, page], { refreshMs: 300_000 });
  const d = q.data;
  const total = d?.total || 0;
  const go = () => { setPage(0); setQuery(qIn.trim()); };
  return (
    <>
      <div className="inst-row">
        <input value={qIn} onChange={(e) => setQIn(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && go()} placeholder="Filter manager name" />
        <button onClick={go}>Search</button>
        {d && <span className="ma-n">{fmt(total)} managers filed {d.quarter}</span>}
      </div>
      {q.initialLoading ? <div className="es-empty"><Loader2 size={18} className="spin" /> Loading…</div>
        : !d || d.status === 'UNAVAILABLE' ? <Unavailable d={d} />
        : (
          <>
            <div style={{ overflowX: 'auto' }}>
              <table className="ma-tbl">
                <thead><tr><th className="l">Manager</th><th>Holdings</th><th>Securities</th><th>Latest filing</th></tr></thead>
                <tbody>
                  {(d.rows || []).map((r: any) => (
                    <tr key={r.cik}>
                      <td className="l">{r.manager_name}</td><td>{fmt(r.holdings)}</td>
                      <td>{r.securities_held}</td><td>{r.latest_filing_date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="inst-pager">
              <button disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>← Prev</button>
              <span>Page {page + 1} of {Math.max(1, Math.ceil(total / limit))}</span>
              <button disabled={(page + 1) * limit >= total} onClick={() => setPage((p) => p + 1)}>Next →</button>
            </div>
          </>
        )}
    </>
  );
}

/* --------------------------------------------------------------- History */
function History() {
  const [tickIn, setTickIn] = useState('NVDA');
  const [tick, setTick] = useState('NVDA');
  const q = useApi<any>((s) => api2.instHistory(tick, s), [tick], { refreshMs: 300_000 });
  const d = q.data;
  const go = () => setTick(tickIn.trim().toUpperCase());
  const series: any[] = d?.series || [];
  const mx = series.reduce((a, s) => Math.max(a, s.reported_shares), 0) || 1;
  return (
    <>
      <div className="inst-row">
        <input value={tickIn} onChange={(e) => setTickIn(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && go()} placeholder="Ticker" />
        <button onClick={go}>Show</button>
      </div>
      {q.initialLoading ? <div className="es-empty"><Loader2 size={18} className="spin" /> Loading…</div>
        : !d || d.status === 'UNAVAILABLE' ? <Unavailable d={d} />
        : d.status === 'UNKNOWN_TICKER' ? <div className="ma-note">No institutional mapping for “{d.ticker}”.</div>
        : (
          <>
            <div className="ma-h" style={{ margin: '4px 0 10px' }}>{tick} reported institutional shares by quarter</div>
            {series.map((s) => (
              <div className="inst-row" key={s.quarter}>
                <span style={{ width: 70 }}>{s.quarter}</span>
                <span className="inst-bar"><i style={{ width: `${(s.reported_shares / mx * 100).toFixed(0)}%` }} /></span>
                <span>{fmt(s.reported_shares)} sh · {fmt(s.holders)} holders · filed {s.latest_filing_date}</span>
              </div>
            ))}
            <div className="inst-note">Quarterly reported holdings only (no fabricated quarters). {d.meta?.historical_identity_note}</div>
          </>
        )}
    </>
  );
}

/* --------------------------------------------------------------- Mapping */
function Mapping() {
  const q = useApi<any>((s) => api2.instMapping('', 200, 0, s), [], { refreshMs: 600_000 });
  const d = q.data;
  if (q.initialLoading) return <div className="es-empty"><Loader2 size={18} className="spin" /> Loading…</div>;
  if (!d || d.status === 'UNAVAILABLE') return <Unavailable d={d} />;
  const counts = d.confidence_counts || {};
  return (
    <>
      <div className="inst-row">
        <span className="ma-n">Confidence:</span>
        {Object.entries(counts).map(([k, n]) => (
          <span className={`inst-conf ${k}`} key={k}>{k} {n as number}</span>
        ))}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="ma-tbl">
          <thead><tr>
            <th className="l">Ticker</th><th className="l">Issuer (SEC)</th><th className="l">CUSIP</th>
            <th className="l">Type</th><th className="l">Exch</th><th className="l">Confidence</th><th className="l">Status</th>
          </tr></thead>
          <tbody>
            {(d.rows || []).map((r: any) => (
              <tr key={r.cusip}>
                <td className="l"><b>{r.ticker || '—'}</b></td>
                <td className="l">{r.sec_issuer_name}</td>
                <td className="l">{r.cusip}</td>
                <td className="l">{r.security_type || '—'}</td>
                <td className="l">{r.exchange || '—'}</td>
                <td className="l"><span className={`inst-conf ${r.confidence}`}>{r.confidence}</span></td>
                <td className="l ma-n">{r.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="inst-note">
        SEC issuer names &amp; CUSIPs preserved. HIGH = exact CUSIP + name match; MEDIUM = CUSIP match, name variant (not auto-promoted).
      </div>
    </>
  );
}

export default function InstitutionalPage() {
  const [tab, setTab] = useState('overview');
  return (
    <div className="page inst-wrap">
      <div className="ts-head">
        <h1><Building2 size={20} /> Institutional Intelligence</h1>
        <p>Quarterly SEC 13F institutional ownership — informational only, independent of the
          Trdgo BUY/SELL trading signals.</p>
      </div>
      <Disclosure />
      <div className="inst-tabs">
        {TABS.map(([k, l]) => (
          <button className={`inst-tab ${tab === k ? 'on' : ''}`} key={k} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {tab === 'overview' && <Overview />}
      {tab === 'stock' && <StockAnalyzer />}
      {tab === 'rankings' && <Rankings />}
      {tab === 'managers' && <Managers />}
      {tab === 'history' && <History />}
      {tab === 'mapping' && <Mapping />}
    </div>
  );
}
