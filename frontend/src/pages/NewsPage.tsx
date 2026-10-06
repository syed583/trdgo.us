import React, { useMemo, useState } from 'react';
import { RefreshCw, Search } from 'lucide-react';
import { api2 } from '../api/client';
import { safeHref } from '../lib/format';
import type { PageContext } from '../App';
import { useApi } from '../hooks/useApi';
import { Panel } from '../components/common';
import { ErrorState, Loading, PageHead, StatusChip, Unavailable } from './shared';

export default function NewsPage({ ctx }: { ctx: PageContext }) {
  const { symbol } = ctx;
  const [openId, setOpenId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<'All' | 'Positive' | 'Neutral' | 'Negative' | 'No score'>('All');

  const news = useApi<any>((s) => api2.news(symbol, 40, s), [symbol]);
  const sentiment = useApi<any>((s) => api2.sentiment(symbol, s), [symbol]);

  const allItems: any[] = news.data?.items || [];
  const sent = sentiment.data;

  // Search across this stock's own headlines, and filter by the same tone
  // buckets the sentiment panel counts -- so a reader can pull up just the
  // positive or just the negative news on the name they are looking at.
  const items = useMemo(() => {
    let rows = allItems;
    if (filter !== 'All') {
      const want = filter === 'No score' ? 'unscored' : filter.toLowerCase();
      rows = rows.filter((n) => (n.sentiment || 'unscored') === want);
    }
    const needle = query.trim().toLowerCase();
    if (needle) {
      rows = rows.filter((n) => String(n.headline || '').toLowerCase().includes(needle));
    }
    return rows;
  }, [allItems, filter, query]);

  const FILTERS = ['All', 'Positive', 'Neutral', 'Negative', 'No score'] as const;

  return (
    <div className="page">
      <PageHead
        title={`News & Sentiment · ${symbol}`}
        subtitle="Headlines from the market data feed, each carrying the tickers it is about. Nothing is scraped."
        right={
          <button className="ghost-btn" onClick={() => { news.refresh(); sentiment.refresh(); }}>
            <RefreshCw size={12} className={news.loading ? 'spin' : undefined} />
            Refresh
          </button>
        }
      />

      <div className="nd-controls">
        <span className="nd-search">
          <Search size={12} color="var(--text-mute)" />
          <input value={query} placeholder={`Search ${symbol} headlines`}
            onChange={(e) => setQuery(e.target.value)} />
        </span>
        {FILTERS.map((f) => (
          <button key={f} className={`nd-pill ${filter === f ? 'active' : ''}`}
            onClick={() => setFilter(f)}>{f}</button>
        ))}
        <span className="nd-count">{items.length} shown</span>
      </div>

      <div className="news-grid">
        <Panel
          title={`Headlines (${items.length})`}
          noBody
          right={<StatusChip status={news.data?.status} />}
        >
          {news.error ? <ErrorState error={news.error} />
            : news.initialLoading ? <Loading />
              : !items.length ? (
                allItems.length ? (
                  <div className="nd-empty">No {symbol} headline matches that filter.</div>
                ) : (
                  <Unavailable
                    status={news.data?.status}
                    detail={news.data?.detail}
                    required={news.data?.status === 'PROVIDER_NOT_CONFIGURED'
                      ? 'A market data key (UNUSUAL_WHALES_API_KEY)'
                      : undefined}
                  />
                )
              ) : (
                <div className="news-list">
                  {items.map((n) => {
                    const id = String(n.id ?? n.article_id ?? n.headline);
                    return (
                      <NewsItem
                        key={id}
                        item={n}
                        open={openId === id}
                        onToggle={() => setOpenId(openId === id ? null : id)}
                      />
                    );
                  })}
                </div>
              )}
        </Panel>

        <div className="stack">
          <Panel title="Headline Sentiment" noBody
            right={<StatusChip status={sent?.status} />}>
            {sentiment.initialLoading ? <Loading />
              : sent?.status === 'OK' ? (
                <>
                  <div className="sent-block">
                    <div className="sent-score-big" style={{
                      color: sent.label === 'BULLISH' ? 'var(--green)'
                        : sent.label === 'BEARISH' ? 'var(--red)' : 'var(--amber)',
                    }}>
                      {sent.score}
                      <span>/100</span>
                    </div>
                    <div className="sent-label-big" style={{
                      color: sent.label === 'BULLISH' ? 'var(--green)'
                        : sent.label === 'BEARISH' ? 'var(--red)' : 'var(--amber)',
                    }}>{sent.label}</div>
                  </div>
                  {String(sent.source || '').includes('Claude') && sent.detail && (
                    <div className="sent-read">{sent.detail}</div>
                  )}
                  <div className="kv">
                    {!String(sent.source || '').includes('Claude') && (
                      <>
                        <div className="kv-row">
                          <span className="k">Positive terms</span>
                          <span className="v pos">{sent.positive_terms}</span>
                        </div>
                        <div className="kv-row">
                          <span className="k">Negative terms</span>
                          <span className="v neg">{sent.negative_terms}</span>
                        </div>
                      </>
                    )}
                    <div className="kv-row">
                      <span className="k">Headlines scored</span>
                      <span className="v">{sent.headlines_scored}/{sent.headlines_total}</span>
                    </div>
                  </div>
                  <div className="hint">{String(sent.source || '').includes('Claude')
                    ? 'Read by Claude from the headline text only — no prices or outside facts added.'
                    : `${sent.method}. Derived only from entitled headline text — no model, no external source.`}</div>
                </>
              ) : (
                <Unavailable status={sent?.status} detail={sent?.detail} compact />
              )}
          </Panel>

          <Panel title="Providers" noBody>
            {news.data?.status === 'OK' ? (
              <>
                <div className="kv">
                  {Array.from(new Set(items.map((i) => i.provider))).map((p) => (
                    <div className="kv-row" key={String(p)}>
                      <span className="k">{String(p)}</span>
                      <span className="v pos">OK</span>
                    </div>
                  ))}
                </div>
                <div className="hint">
                  Headlines are read on request rather than streamed, and each
                  links out to its publisher.
                </div>
              </>
            ) : <Unavailable status={news.data?.status} compact />}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function NewsItem({
  item, open, onToggle,
}: { item: any; open: boolean; onToggle: () => void }) {
  // Every headline links out to its publisher. The article-text endpoint
  // that used to render a body inline belonged to the broker news wire,
  // is gone; a headline with no link now says so rather than spinning on a
  // fetch that cannot be served.
  const linked = !!item.url;

  const stamp = item.published_at || item.time;
  const parsed = stamp ? new Date(stamp) : null;
  const when = parsed && !Number.isNaN(parsed.getTime()) ? parsed : null;

  return (
    <div className={`news-item ${open ? 'open' : ''}`}>
      <button className="news-head" onClick={onToggle}>
        <span className="news-time">
          {when ? when.toLocaleString('en-US', {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
          }) : '--'}
        </span>
        <span className="news-title">{item.headline}</span>
        {item.sentiment && item.sentiment !== 'unscored' && (
          <span className={`nd-badge ${item.sentiment}`}>{item.sentiment}</span>
        )}
        <span className="news-prov">{item.provider_code || item.provider}</span>
      </button>

      {open && (
        <div className="news-body">
          {linked ? (
            <div className="news-text">
              {item.summary && <p>{item.summary}</p>}
              <a href={safeHref(item.url)} target="_blank" rel="noopener noreferrer">Read the full article at {item.provider || 'source'} →</a>
            </div>
          ) : (
            <div className="news-text">
              {item.summary && <p>{item.summary}</p>}
              <Unavailable
                status="NO_LINK"
                detail="This headline arrived without a link to its source."
                compact
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
