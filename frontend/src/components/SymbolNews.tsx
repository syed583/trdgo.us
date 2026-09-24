import React from 'react';
import { ExternalLink, Newspaper } from 'lucide-react';
import { Panel } from './common';
import { api2 } from '../api/client';
import { safeHref } from '../lib/format';
import { useApi } from '../hooks/useApi';

/**
 * Headlines for one symbol.
 *
 * The tone beside each headline is a keyword estimate over the headline
 * text, not a provider's sentiment model, and the footer says so. The
 * distinction matters: a counted score and a modelled one are different
 * claims, and only one of them has read the article.
 */
export default function SymbolNews({ symbol }: { symbol: string }) {
  const news = useApi<any>((s) => api2.news(symbol, 20, s), [symbol]);
  const d = news.data;
  const items: any[] = d?.items || [];

  return (
    <Panel title={`${symbol} News`} icon={<Newspaper size={13} />} noBody
      right={d?.source ? <span className="badge gray">{d.source}</span> : undefined}>
      {news.initialLoading ? (
        <p className="sn-note">Reading the wire…</p>
      ) : !items.length ? (
        <p className="sn-note">
          {d?.detail || `No headlines on record for ${symbol}.`}
        </p>
      ) : (
        <div className="sn-list">
          {items.map((n) => (
            <article className="sn-row" key={n.id || n.headline}>
              <div className="sn-meta">
                <span className={`sn-tone sn-${(n.sentiment || 'neutral').toLowerCase()}`}>
                  {(n.sentiment || 'NEUTRAL').toLowerCase()}
                </span>
                <em>{n.time_label || ''}</em>
                {n.provider && <span className="sn-src">{n.provider}</span>}
              </div>
              <div className="sn-head">
                {n.url ? (
                  <a href={safeHref(n.url)} target="_blank" rel="noopener noreferrer">
                    {n.headline} <ExternalLink size={10} />
                  </a>
                ) : n.headline}
              </div>
              {n.symbols?.length > 1 && (
                <div className="sn-tickers">
                  {n.symbols.slice(0, 5).map((t: string) => (
                    <span key={t} className={t === symbol ? 'on' : undefined}>{t}</span>
                  ))}
                </div>
              )}
            </article>
          ))}
        </div>
      )}
      <p className="sn-foot">
        {d?.tone_basis
          || 'Tone is a keyword estimate over the headline, not a provider model.'}
      </p>
    </Panel>
  );
}
