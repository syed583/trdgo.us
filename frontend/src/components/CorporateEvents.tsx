import React, { useState } from 'react';
import { Building2, ExternalLink } from 'lucide-react';
import { Panel } from './common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';

/**
 * What the company told the SEC happened: management changes, deals, funding.
 *
 * The events come from the item numbers the company itself tagged on its 8-K,
 * so this is the issuer's own classification rather than a reading of the
 * news. The tag says what kind of event it was and not the particulars, which
 * is why every line links to the filing instead of paraphrasing it.
 */
interface Event {
  filed: string;
  days_ago: number | null;
  form: string;
  item: string | null;
  category: string;
  source?: string;
  live?: boolean;
  category_label: string;
  headline: string;
  detail: string;
  impact: string;
  url: string | null;
  facts?: { label: string; value: string }[];
}

// The order the tabs appear in; any category the feed returns that is not
// listed here still gets a tab, appended, so no event is unreachable.
const LABELS: Record<string, string> = {
  deal: 'Mergers & deals',
  leadership: 'Management',
  funding: 'Funding & debt',
  earnings: 'Earnings',
  dividend: 'Dividends',
  restructuring: 'Restructuring',
  governance: 'Governance',
  distress: 'Distress',
  listing: 'Listing',
  disclosure: 'Company statements',
};
const ORDER = Object.keys(LABELS);

function tabsFor(counts: Record<string, number>, events: Event[]) {
  const present = Object.keys(counts).filter((k) => counts[k] > 0);
  const known = ORDER.filter((k) => present.includes(k));
  const extra = present.filter((k) => !ORDER.includes(k));
  return [...known, ...extra].map((key) => ({
    key,
    n: counts[key],
    label: LABELS[key]
      || events.find((e) => e.category === key)?.category_label
      || key,
  }));
}

function ago(days: number | null): string {
  if (days == null) return '';
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.round(days / 30)}mo ago`;
  return `${Math.round(days / 365)}y ago`;
}

export default function CorporateEvents({ symbol }: { symbol: string }) {
  const [filter, setFilter] = useState('all');
  const [open, setOpen] = useState<string | null>(null);
  const events = useApi<any>((s) => api2.corporateEvents(symbol, s), [symbol]);
  const d = events.data;

  if (events.initialLoading) {
    return <Panel title="Company Events"><p className="ce-note">Reading SEC filings…</p></Panel>;
  }
  if (!d || d.status !== 'OK') {
    return (
      <Panel title="Company Events">
        <p className="ce-note">{d?.detail || 'No filings available for this symbol.'}</p>
      </Panel>
    );
  }

  const all: Event[] = d.events || [];
  const rows: Event[] = all.filter(
    (e: Event) => filter === 'all' || e.category === filter);
  const tabs = tabsFor(d.counts_by_category || {}, all);

  return (
    <Panel title="Company Events" noBody
      right={<span className="ce-count">{d.count} in 12 months</span>}>
      <div className="ce">
        <div className="ce-tabs">
          <button className={`ce-tab ${filter === 'all' ? 'active' : ''}`}
            onClick={() => setFilter('all')}>
            All <em>{all.length}</em>
          </button>
          {tabs.map((f) => (
            <button key={f.key}
              className={`ce-tab ${filter === f.key ? 'active' : ''}`}
              onClick={() => setFilter(f.key)}>
              {f.label} <em>{f.n}</em>
            </button>
          ))}
        </div>

        <div className="ce-list">
          {rows.length === 0 && <p className="ce-note">Nothing filed under this heading.</p>}
          {rows.map((e, i) => {
            const id = `${e.filed}-${e.item}-${i}`;
            const facts = e.facts || [];
            const isOpen = open === id;
            return (
            <div className={`ce-row ce-${e.impact} ${isOpen ? 'ce-open' : ''}`} key={id}
              role={facts.length ? 'button' : undefined}
              tabIndex={facts.length ? 0 : undefined}
              onClick={() => facts.length && setOpen(isOpen ? null : id)}
              onKeyDown={(ev) => {
                if (facts.length && (ev.key === 'Enter' || ev.key === ' ')) {
                  ev.preventDefault();
                  setOpen(isOpen ? null : id);
                }
              }}>
              <div className="ce-when">
                <b>{e.filed}</b>
                <em>{ago(e.days_ago)}</em>
              </div>
              <div className="ce-what">
                <div className="ce-head">
                  <Building2 size={12} />
                  <b>{e.headline}</b>
                  <span className={`ce-pill ce-pill-${e.impact}`}>{e.impact}</span>
                  <span className="ce-form">{e.form}{e.item ? ` · item ${e.item}` : ''}</span>
                {e.source === 'BENZINGA' && <span className="ce-src">Benzinga</span>}
                {(e as any).live && <span className="ce-live">just filed</span>}
                </div>
                <p>{e.detail}</p>
                {isOpen && facts.length > 0 && (
                  <dl className="ce-facts">
                    {facts.map((f) => (
                      <div key={f.label}>
                        <dt>{f.label}</dt>
                        <dd>{f.value}</dd>
                      </div>
                    ))}
                  </dl>
                )}
                {facts.length > 0 && (
                  <span className="ce-more">
                    {isOpen ? 'Hide details' : 'Show details'}
                  </span>
                )}
              </div>
              {e.url && (
                <a className="ce-link" href={e.url} target="_blank"
                  rel="noopener noreferrer" onClick={(ev) => ev.stopPropagation()}>
                  Filing <ExternalLink size={11} />
                </a>
              )}
            </div>
            );
          })}
        </div>

        <p className="ce-foot">
          {d.detail} Source: SEC EDGAR, with Benzinga for deal, offering and
          dividend particulars.
        </p>
      </div>
    </Panel>
  );
}
