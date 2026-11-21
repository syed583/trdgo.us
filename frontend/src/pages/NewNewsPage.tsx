import React from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle, ArrowLeft, ExternalLink, Newspaper, RefreshCw, Search, ShieldAlert,
} from 'lucide-react';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';
import type { PageContext } from '../App';
import '../new-dashboard.css';
import '../new-strategy.css';

/**
 * News & Sentiment in the redesigned shell (Figma frame 24:2).
 *
 * THE PAGE NEVER READS THE SENTIMENT SOURCE STRING. /api/sentiment/{symbol} returns
 * `source: "Claude · headline read"` and the route applies no model_privacy wrap, so the vendor's name
 * reaches the client. The page it replaces branched on `sent.source.includes('Claude')`, which both
 * depended on the leak and kept it alive. This page describes the method in its own words instead, so
 * fixing the server cannot break it.
 *
 * "POSITIVE TERMS / NEGATIVE TERMS" IS NOT WHAT WE MEASURE. The frame counts terms; the payload counts
 * HEADLINES by tone. Those are different things, and labelling a headline count as a term count would
 * misdescribe the measurement, so the counters here say headlines.
 *
 * Nothing is scraped: the headlines come from the market data feed.
 *
 * THEY DO NOT LINK OUT. The page used to promise that every headline linked to its publisher;
 * checked against the live feed on 2026-11-20, `url` is null on every item, so the promise was
 * false and is gone. A headline becomes a link only where a URL actually arrives. Why the feed
 * sends none is a separate question the owner is picking up.
 */

type Any = any;

const money = (v: Any): string =>
  (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : `$${Number(v).toFixed(2)}`);
const pct = (v: Any): string =>
  (v === null || v === undefined ? '' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`);

const TONE_OF = (v: Any): 'positive' | 'negative' | 'neutral' | 'none' => {
  const s = String(v ?? '').toUpperCase();
  if (s.includes('POS') || s.includes('BULL')) return 'positive';
  if (s.includes('NEG') || s.includes('BEAR')) return 'negative';
  if (s.includes('NEUTRAL')) return 'neutral';
  return 'none';
};
const TONE_CLASS: Record<string, string> = {
  positive: 'win', negative: 'loss', neutral: 'flat', none: '',
};

export default function NewNewsPage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();
  const isAdmin = !!ctx?.isAdmin;
  const symbol = (ctx?.symbol || '').toUpperCase();

  const [find, setFind] = React.useState('');
  const [tone, setTone] = React.useState<'all' | 'positive' | 'neutral' | 'negative' | 'none'>('all');
  const [nonce, setNonce] = React.useState(0);

  const news = useApi<Any>((s) => api2.news(symbol, 40, s), [symbol, nonce]);
  const sent = useApi<Any>((s) => api2.sentiment(symbol, s), [symbol, nonce]);
  const alerts = useApi<Any>((s) => api2.earningsAlerts(symbol, s), [symbol, nonce]);

  const items: Any[] = news.data?.items || news.data?.articles || [];
  const s = sent.data || {};
  const quote = ctx?.quote?.data || {};

  const shown = React.useMemo(() => {
    let r = items;
    if (tone !== 'all') r = r.filter((x) => TONE_OF(x.sentiment ?? x.tone) === tone);
    const f = find.trim().toLowerCase();
    if (f) r = r.filter((x) => String(x.headline || '').toLowerCase().includes(f));
    return r;
  }, [items, tone, find]);

  // Counted from the headlines themselves - these are HEADLINES, not terms.
  const counts = React.useMemo(() => {
    const c = { positive: 0, negative: 0, neutral: 0, none: 0 };
    items.forEach((x) => { c[TONE_OF(x.sentiment ?? x.tone)] += 1; });
    return c;
  }, [items]);

  const providers = React.useMemo(
    () => Array.from(new Set(items.map((i) => i.provider).filter(Boolean))),
    [items],
  );

  const alertRows: Any[] = alerts.data?.rows || alerts.data?.alerts || [];
  const scoreTone = s.label === 'BULLISH' ? 'up' : s.label === 'BEARISH' ? 'down' : '';

  // 24:263-24:269 - the frame pages the headlines twenty at a time. We were rendering all of them,
  // which is the entire height the card ran over.
  const PER_PAGE = 20;
  const [page, setPage] = React.useState(1);
  React.useEffect(() => { setPage(1); }, [tone, find, symbol]);
  const pageCount = Math.max(1, Math.ceil(shown.length / PER_PAGE));
  const pageSafe = Math.min(page, pageCount);
  const pageRows = shown.slice((pageSafe - 1) * PER_PAGE, pageSafe * PER_PAGE);
  const firstShown = shown.length ? (pageSafe - 1) * PER_PAGE + 1 : 0;
  const lastShown = Math.min(pageSafe * PER_PAGE, shown.length);

  return (
    <div className="nd nst nua nnw">
      <section className="nua-head nnw-head">
        <button type="button" className="nnw-back" onClick={() => navigate(-1)} aria-label="Back">
          <ArrowLeft size={18} />
        </button>
        <div>
          <h1><Newspaper size={22} /> News &amp; Sentiment{symbol ? ` — ${symbol}` : ''}</h1>
          <p>
            Headlines from the market data feed, each carrying the tickers it is about. Nothing is
            scraped.
          </p>
        </div>
        <button type="button" className="nnw-refresh" onClick={() => setNonce((n) => n + 1)}>
          <RefreshCw size={15} /> Refresh
        </button>
      </section>

      {/* ---------------------------------------------------------------- symbol + score */}
      <div className="nnw-top">
        <section className="nd-card nnw-quote">
          <div>
            <b className="nnw-sym">{symbol || '—'}</b>
            <span className="nst-mute">{quote.name || ''}</span>
          </div>
          <div className="nnw-price">
            <b>{money(quote.price)}</b>
            {quote.change_percent != null && (
              <i className={Number(quote.change_percent) >= 0 ? 'up' : 'down'}>
                {quote.change != null ? `${Number(quote.change) >= 0 ? '+' : ''}${Number(quote.change).toFixed(2)} ` : ''}
                ({pct(quote.change_percent)})
              </i>
            )}
          </div>
        </section>

        <section className="nd-card nnw-score">
          <header className="nd-card-head"><h3>Sentiment score</h3></header>
          {s.status === 'OK' && s.score != null ? (
            <div className="nnw-score-body">
              <b className={scoreTone}>{s.score}<small>/100</small></b>
              <span className={`nst-status ${s.label === 'BULLISH' ? 'win' : s.label === 'BEARISH' ? 'loss' : 'flat'}`}>
                {s.label ? s.label.charAt(0) + s.label.slice(1).toLowerCase() : '—'}
              </span>
            </div>
          ) : (
            <div className="nd-empty">
              {sent.loading ? 'Reading the headlines…'
                : s.status === 'INSUFFICIENT_DATA' ? 'Too few headlines to score.'
                  : 'No reading right now.'}
            </div>
          )}
        </section>
      </div>

      {/* ---------------------------------------------------------------- filters */}
      <div className="nst-filters">
        <div className="nd-tabs">
          {(['all', 'positive', 'neutral', 'negative', 'none'] as const).map((k) => (
            <button type="button" key={k} className={tone === k ? 'is-on' : ''} onClick={() => setTone(k)}>
              {k === 'all' ? 'All' : k === 'none' ? 'No score' : k.charAt(0).toUpperCase() + k.slice(1)}
              {k !== 'all' && !!counts[k] && <span className="nnw-n">{counts[k]}</span>}
            </button>
          ))}
        </div>
        <label className="nst-field nst-find">
          <span>Search headlines</span>
          <span className="nsh-lookup">
            <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="any word" />
            <Search size={15} />
          </span>
        </label>
        <span className="nst-count">{shown.length} shown of {items.length}</span>
      </div>

      {/* ---------------------------------------------------------------- headlines */}
      <section className="nd-card nst-card">
        <header className="nd-card-head nsk-head"><h3>Headlines{items.length ? ` (${items.length})` : ''}</h3></header>
        {news.loading && !items.length && <div className="nd-empty">Loading…</div>}
        {!news.loading && !items.length && <div className="nd-empty">No headlines for this symbol right now.</div>}
        {!!items.length && !shown.length && <div className="nd-empty">Nothing matches this filter.</div>}

        {!!shown.length && (
          <ul className="nnw-list">
            {pageRows.map((n, i) => {
              const t = TONE_OF(n.sentiment ?? n.tone);
              return (
                <li key={String(n.id ?? n.url ?? i)}>
                  <span className="nnw-time">{n.time_label || n.time || n.published_at || ''}</span>
                  <a className="nnw-headline" href={n.url || undefined} target="_blank"
                     rel="noopener noreferrer"
                     onClick={(e) => { if (!n.url) e.preventDefault(); }}>
                    {n.headline}
                    {!!n.url && <ExternalLink size={12} />}
                  </a>
                  {t !== 'none' && (
                    <span className={`nst-status ${TONE_CLASS[t]}`}>
                      {t.charAt(0).toUpperCase() + t.slice(1)}
                    </span>
                  )}
                  {!!n.provider && <span className="nnw-prov">{n.provider}</span>}
                </li>
              );
            })}
          </ul>
        )}

        {/* 24:263-24:269 - "Showing 1-20 of 40" and the page buttons. Only when there is more
            than one page: a pager over a single page is furniture, not a control. */}
        {shown.length > PER_PAGE && (
          <div className="nnw-pager">
            <span className="nst-mute">
              Showing {firstShown}&ndash;{lastShown} of {shown.length} headlines
            </span>
            <div className="nnw-pager-btns">
              {Array.from({ length: pageCount }, (_, i) => i + 1).map((n) => (
                <button type="button" key={n} className={n === pageSafe ? 'is-on' : ''}
                  aria-current={n === pageSafe ? 'page' : undefined}
                  onClick={() => setPage(n)}>{n}</button>
              ))}
              <button type="button" aria-label="Next page" disabled={pageSafe >= pageCount}
                onClick={() => setPage((p) => Math.min(pageCount, p + 1))}>&rsaquo;</button>
            </div>
          </div>
        )}
      </section>

      {/* ---------------------------------------------------------------- bottom row */}
      <div className="nnw-bottom">
        <section className="nd-card">
          <header className="nd-card-head"><h3>Headline sentiment</h3></header>
          {s.status === 'OK' ? (
            <>
              <div className="nnw-score-body">
                <b className={scoreTone}>{s.score}<small>/100</small></b>
                <span className={`nst-status ${s.label === 'BULLISH' ? 'win' : s.label === 'BEARISH' ? 'loss' : 'flat'}`}>
                  {s.label ? s.label.charAt(0) + s.label.slice(1).toLowerCase() : '—'}
                </span>
              </div>
              <table className="nd-table">
                <tbody>
                  <tr><td>Positive headlines</td><td className="r"><b>{counts.positive}</b></td></tr>
                  <tr><td>Negative headlines</td><td className="r"><b>{counts.negative}</b></td></tr>
                  <tr><td>Neutral headlines</td><td className="r"><b>{counts.neutral}</b></td></tr>
                  <tr>
                    {/* This fraction is the SENTIMENT service's own window, which is not the list
                        above: the page showed "8 / 20" beside a list of 40 and the two looked like
                        a contradiction. Saying whose window it is costs one line. */}
                    <td>Headlines scored<div className="nnw-sub">in the sentiment window</div></td>
                    <td className="r"><b>{s.headlines_scored ?? '—'} / {s.headlines_total ?? items.length}</b></td>
                  </tr>
                </tbody>
              </table>
              {/* The method, in our own words - not the payload's attribution string, which still
                  carries a vendor name the route does not scrub. */}
              <p className="nd-note">
                Read from the headline text alone — no prices, no outside facts, and nothing scraped.
              </p>
            </>
          ) : (
            <div className="nd-empty">
              {sent.loading ? 'Loading…' : 'Nothing scored for this symbol.'}
            </div>
          )}
        </section>

        <section className="nd-card">
          <header className="nd-card-head"><h3><ShieldAlert size={16} /> Filings &amp; risk alerts</h3></header>
          {alerts.loading && !alertRows.length && <div className="nd-empty">Loading…</div>}
          {!alerts.loading && !alertRows.length && <div className="nd-empty">No filings or alerts for this symbol.</div>}
          {!!alertRows.length && (
            <ul className="nnw-alerts">
              {alertRows.slice(0, 8).map((a: Any, i: number) => (
                <li key={i}>
                  <span className={`nnw-sev ${String(a.severity || '').toLowerCase()}`}>{a.severity || '—'}</span>
                  <div>
                    <b>{a.title}</b>
                    {!!a.detail && <p>{a.detail}</p>}
                    <i>{[a.source, a.date].filter(Boolean).join(' · ')}</i>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

      </div>

      {/* 24:11 — Providers is its own full-width row under the two cards, not a third column.
          Its note was an EMPTY <p>: it used to carry the frame's "each links out to its
          publisher", which was removed because some feed items have no URL. Correct to remove,
          but what was left said nothing at all. */}
      <section className="nd-card nnw-provs-card">
        <header className="nd-card-head"><h3>Providers</h3></header>
        {providers.length ? (
          <ul className="nnw-provs">
            {providers.map((p) => <li key={String(p)}>{String(p)}</li>)}
          </ul>
        ) : <div className="nd-empty">No provider named on these headlines.</div>}
        <p className="nd-note">
          Headlines are read on request rather than streamed. Where the feed carries a link to the
          publisher the headline opens it; some items arrive without one, and those do not.
        </p>
      </section>

      {isAdmin && (
        <details className="nd-ops">
          <summary>Feed detail (administrator)</summary>
          <div>
            news {news.data?.status || '—'} · sentiment {s.status || '—'} ·
            scored {s.headlines_scored ?? '—'}/{s.headlines_total ?? '—'} ·
            alerts {alerts.data?.status || '—'} · providers {providers.length}
          </div>
        </details>
      )}

      {(news.error || sent.error) && (
        <div className="nd-err"><AlertTriangle size={15} /> The news feed could not be loaded.</div>
      )}
    </div>
  );
}
