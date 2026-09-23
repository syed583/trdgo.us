import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Activity, ExternalLink, Hash, Layers, Newspaper, RefreshCw, Search,
} from 'lucide-react';
import { api2 } from '../api/client';
import type { PageContext } from '../App';
import { useApi } from '../hooks/useApi';
import { Panel } from '../components/common';
import { ErrorState, Loading } from './shared';

/**
 * News & Sentiment.
 *
 * Every tally on the page -- the split, the tickers, the sources, the keywords
 * and the sector read -- is computed from the same articles shown in the feed,
 * so no panel can contradict the list beside it.
 *
 * The provider returns three articles per request against a daily quota, so
 * the feed is a sample rather than the whole wire, and the page says so where
 * a reader would otherwise assume completeness.
 */

const FILTERS = ['All', 'Positive', 'Neutral', 'Negative'] as const;
type Filter = typeof FILTERS[number];

/** A half-dial for the aggregate sentiment reading. */
function Dial({ value, label }: { value: number | null; label: string }) {
  const v = value == null ? 50 : Math.max(0, Math.min(100, value));
  const angle = (v - 50) / 50 * 90;
  const colour = v >= 60 ? 'var(--green)' : v <= 40 ? 'var(--red)' : 'var(--amber)';
  return (
    <div className="nd-dial">
      <svg viewBox="0 0 120 68" width="132" height="76">
        <path d="M 10 60 A 50 50 0 0 1 40 14" fill="none"
          stroke="var(--red)" strokeWidth="9" strokeLinecap="round" />
        <path d="M 46 11 A 50 50 0 0 1 74 11" fill="none"
          stroke="var(--amber)" strokeWidth="9" strokeLinecap="round" />
        <path d="M 80 14 A 50 50 0 0 1 110 60" fill="none"
          stroke="var(--green)" strokeWidth="9" strokeLinecap="round" />
        <g transform={`rotate(${angle} 60 60)`}>
          <line x1="60" y1="60" x2="60" y2="24" stroke={colour}
            strokeWidth="3" strokeLinecap="round" />
        </g>
        <circle cx="60" cy="60" r="4.5" fill={colour} />
      </svg>
      <b style={{ color: colour }}>{value ?? '--'}</b>
      <i>{label}</i>
    </div>
  );
}

/** Daily positive / neutral / negative counts as stacked columns. */
function TrendChart({ trend }: { trend: any[] }) {
  if (!trend.length) return null;
  const peak = Math.max(1, ...trend.map((t) => t.total));
  return (
    <>
      <div className="nd-trend">
        {trend.map((t) => (
          <div className="nd-trend-col" key={t.date}
            title={`${t.date}: ${t.positive} positive, ${t.neutral} neutral, ${t.negative} negative`}>
            <span className="nd-trend-stack">
              <i className="pos" style={{ height: `${t.positive / peak * 100}%` }} />
              <i className="neu" style={{ height: `${t.neutral / peak * 100}%` }} />
              <i className="neg" style={{ height: `${t.negative / peak * 100}%` }} />
            </span>
            <em>{t.date.slice(5)}</em>
          </div>
        ))}
      </div>
      <div className="nd-legend">
        <span><i className="pos" />Positive</span>
        <span><i className="neu" />Neutral</span>
        <span><i className="neg" />Negative</span>
      </div>
    </>
  );
}

export default function NewsDeskPage({ ctx }: { ctx: PageContext }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('All');

  const desk = useApi<any>((s) => api2.newsDesk(s), []);
  const d = desk.data;

  const articles = useMemo(() => {
    let rows: any[] = d?.articles || [];
    if (filter !== 'All') rows = rows.filter((a) => a.sentiment === filter.toLowerCase());
    const needle = query.trim().toLowerCase();
    if (needle) {
      rows = rows.filter((a) => (
        String(a.headline || '').toLowerCase().includes(needle)
        || (a.symbols || []).some((s: string) => s.toLowerCase().includes(needle))
      ));
    }
    return rows;
  }, [d, filter, query]);

  if (desk.error) {
    return (
      <div className="page">
        <Panel title="News &amp; Sentiment"><ErrorState error={desk.error} /></Panel>
      </div>
    );
  }
  if (desk.initialLoading || !d) {
    return (
      <div className="page">
        <Panel title="News &amp; Sentiment"><Loading /></Panel>
      </div>
    );
  }
  if (d.status !== 'OK') {
    return (
      <div className="page">
        <Panel title="News &amp; Sentiment">
          <div className="nd-note">{d.detail}</div>
        </Panel>
      </div>
    );
  }

  const s = d.sentiment || {};
  const featured = (d.articles || [])[0];
  const peakKeyword = Math.max(1, ...(d.keywords || []).map((k: any) => k.count));

  return (
    <div className="page">
      <div className="nd-head">
        <div>
          <h1>News &amp; Sentiment</h1>
          <p>
            Headlines across the large-cap tape, with the provider&apos;s
            per-article sentiment tallied by ticker, sector and source.
          </p>
        </div>
        <div className="nd-head-right">
          <span className="nd-live"><i />{d.count} articles</span>
          <button className="ghost-btn" onClick={desk.refresh}>
            <RefreshCw size={12} className={desk.loading ? 'spin' : undefined} />
            Refresh
          </button>
        </div>
      </div>

      <div className="nd-cards">
        <div className="nd-card">
          <span className="nd-card-label">Overall sentiment</span>
          <div className="nd-card-dial">
            <Dial value={s.score ?? null} label={s.label} />
            <div className="nd-split">
              <div><em className="pos" />Positive<b>{s.positive_percent ?? 0}%</b></div>
              <div><em className="neu" />Neutral<b>{s.neutral_percent ?? 0}%</b></div>
              <div><em className="neg" />Negative<b>{s.negative_percent ?? 0}%</b></div>
            </div>
          </div>
        </div>

        <div className="nd-card">
          <span className="nd-card-label">Articles (24h)</span>
          <b className="nd-card-value">{d.last_24h}</b>
          <span className="nd-card-sub">
            of {d.count} retrieved · {d.found?.toLocaleString()} matched
          </span>
        </div>

        <div className="nd-card">
          <span className="nd-card-label">Trending tickers</span>
          <div className="nd-chips">
            {(d.tickers || []).slice(0, 6).map((t: any) => (
              <button className="nd-chip" key={t.symbol}
                onClick={() => navigate(`/news/${t.symbol}${ctx.search}`)}>
                {t.symbol}<em>{t.articles}</em>
              </button>
            ))}
          </div>
        </div>

        <div className="nd-card">
          <span className="nd-card-label">Top sources</span>
          <div className="nd-rows">
            {(d.sources || []).slice(0, 4).map((x: any) => (
              <div className="nd-row" key={x.provider}>
                <span>{x.provider}</span>
                <b>{x.articles}</b>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="nd-controls">
        <span className="nd-search">
          <Search size={12} color="var(--text-mute)" />
          <input value={query} placeholder="Search headlines or tickers"
            onChange={(e) => setQuery(e.target.value)} />
        </span>
        {FILTERS.map((f) => (
          <button key={f} className={`nd-pill ${filter === f ? 'active' : ''}`}
            onClick={() => setFilter(f)}>{f}</button>
        ))}
        <span className="nd-count">{articles.length} shown</span>
      </div>

      <div className="nd-split-main">
        <Panel title="Latest" icon={<Newspaper size={13} />} noBody>
          {!articles.length ? (
            <div className="nd-empty">No article matches that filter.</div>
          ) : (
            <div className="nd-feed">
              {articles.map((a) => (
                <a className="nd-article" key={a.id} href={a.url}
                  target="_blank" rel="noopener noreferrer">
                  <div className="nd-article-body">
                    <b>{a.headline}</b>
                    {a.summary && <p>{a.summary}</p>}
                    <span className="nd-meta">
                      {a.time_label && <em>{a.time_label}</em>}
                      <em>{a.provider}</em>
                    </span>
                    {!!(a.impacts || []).length && (
                      <span className="nd-impacts">
                        <em>Impacts</em>
                        {(a.impacts || []).map((x: any) => (
                          <i key={x.symbol} className={x.sentiment}
                            title={[x.name, x.score != null
                              ? `sentiment ${x.score > 0 ? '+' : ''}${x.score}`
                              : 'no sentiment score'].filter(Boolean).join(' - ')}>
                            {x.symbol}
                            {x.score != null && (
                              <b>{x.score > 0 ? '+' : ''}{x.score.toFixed(2)}</b>
                            )}
                          </i>
                        ))}
                      </span>
                    )}
                  </div>
                  <span className={`nd-badge ${a.sentiment}`}>
                    {a.sentiment === 'unscored' ? 'No score' : a.sentiment}
                  </span>
                </a>
              ))}
            </div>
          )}
          <div className="hint">{d.detail}</div>
        </Panel>

        <div className="nd-side">
          {featured && (
            <Panel title="Featured" icon={<ExternalLink size={13} />}>
              <a className="nd-featured" href={featured.url}
                target="_blank" rel="noopener noreferrer">
                <span className={`nd-badge ${featured.sentiment}`}>
                  {featured.sentiment}
                </span>
                <b>{featured.headline}</b>
                {featured.summary && <p>{featured.summary}</p>}
                <i>{featured.time_label} · {featured.provider}</i>
              </a>
              <div className="hint">
                The most recent article in the feed, not an editorial pick.
              </div>
            </Panel>
          )}

          <Panel title="Sentiment by sector" icon={<Layers size={13} />}>
            {!(d.sectors || []).length ? (
              <div className="nd-note">
                No article in this batch maps to an issuer sector.
              </div>
            ) : (
              <div className="nd-sectors">
                {d.sectors.map((x: any) => (
                  <div className="nd-sector" key={x.sector}>
                    <span>{x.sector}</span>
                    <span className="nd-sector-track">
                      <i className={x.score >= 0 ? 'pos' : 'neg'}
                        style={{ width: `${Math.min(Math.abs(x.score), 100)}%` }} />
                    </span>
                    <b className={x.score >= 0 ? 'pos' : 'neg'}>
                      {x.score > 0 ? '+' : ''}{x.score}
                    </b>
                  </div>
                ))}
              </div>
            )}
            <div className="hint">
              Average article sentiment for the issuers named, on a −100 to
              +100 scale, grouped by the SIC division each files under.
            </div>
          </Panel>

          <Panel title="Trending keywords" icon={<Hash size={13} />}>
            {!(d.keywords || []).length ? (
              <div className="nd-note">
                No word recurs across this batch of headlines.
              </div>
            ) : (
              <div className="nd-cloud">
                {d.keywords.map((k: any) => (
                  <button className="nd-word" key={k.word}
                    onClick={() => setQuery(k.word)}
                    style={{
                      fontSize: `${11 + (k.count / peakKeyword) * 13}px`,
                      opacity: 0.55 + (k.count / peakKeyword) * 0.45,
                    }}>
                    {k.word}
                  </button>
                ))}
              </div>
            )}
            <div className="hint">
              Words recurring across headlines, tickers and filler removed.
              Click one to filter the feed.
            </div>
          </Panel>

          <Panel title="Sentiment trend" icon={<Activity size={13} />}>
            {!(d.trend || []).length ? (
              <div className="nd-note">Not enough dated articles to chart.</div>
            ) : (
              <TrendChart trend={d.trend} />
            )}
            <div className="hint">
              {d.count} articles span {(d.trend || []).length} day
              {(d.trend || []).length === 1 ? '' : 's'} — a short window, because
              the plan returns three articles per request.
            </div>
          </Panel>
        </div>
      </div>

      <div className="hint">{s.detail}</div>
    </div>
  );
}
