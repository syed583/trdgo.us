import React from 'react';
import { Panel } from './common';

/**
 * The model's parameters rolled into headline categories.
 *
 * Shared by the Analysis (Insights) page and the Earnings Analysis panel so
 * both show the identical MARKET & PRICE / COMPANY & OWNERSHIP breakdown from
 * the same signals -- grouped as the model is weighted (eighty points of
 * market and price, twenty of company and ownership), with each category's
 * parameters listed underneath and the points they carry.
 */

const GREEN = '#21d07a';
const RED = '#f2465a';
const AMBER = '#f5a524';

export default function CategoryBreakdown({ categories, signals }: {
  categories?: any[]; signals?: any[];
}) {
  if (!categories?.length) return null;
  const byName: Record<string, any> = Object.fromEntries(
    (signals || []).map((s) => [s.name, s]));

  return (
    <Panel title="Category Breakdown" noBody
      right={<span className="ac-total">100 pts</span>}>
      <div className="ai-cats">
        {categories.map((c, index) => (
          <React.Fragment key={c.key}>
            {(index === 0
              || (categories[index - 1].group || '') !== (c.group || '')) && (
              <div className="ac-group">
                <b>{c.group || 'Other'}</b>
                <em>
                  {categories
                    .filter((x) => (x.group || '') === (c.group || ''))
                    .reduce((t, x) => t + (x.weight_possible || 0), 0)} pts
                </em>
              </div>
            )}
            <div className="ai-cat" title={c.detail || ''}>
              <span className="ac-label">
                {c.label}
                {c.weight_possible != null && (
                  <em className="ac-weight">{c.weight_possible}pt</em>
                )}
              </span>
              <span className="ac-track">
                <i style={{
                  width: `${c.score ?? 0}%`,
                  background: !c.available ? 'var(--panel-2)'
                    : (c.score ?? 50) >= 60 ? GREEN
                      : (c.score ?? 50) <= 40 ? RED : AMBER,
                }} />
              </span>
              <span className="ac-val">
                {c.available ? c.score : '--'}
                {c.available && c.points != null && (
                  <em className={c.points >= 0 ? 'pos' : 'neg'}>
                    {c.points > 0 ? '+' : ''}{c.points}
                  </em>
                )}
                {c.available && c.points == null && (
                  <em className="dim">sizing only</em>
                )}
              </span>
            </div>

            {(c.weight_possible || 0) > 0
              && (c.parameters || []).filter(
                (n: string) => (byName[n]?.weight || 0) > 0).length > 1 && (
              <div className="ac-parts">
                {(c.parameters || [])
                  .map((name: string) => byName[name])
                  .filter(Boolean)
                  .filter((s: any) => (s.weight || 0) > 0)
                  .sort((a: any, b: any) => (b.weight || 0) - (a.weight || 0))
                  .map((s: any) => (
                    <div className="ac-part" key={s.name}>
                      <span>{s.label}<em>{s.weight}pt</em></span>
                      <b className={!s.available ? 'dim'
                        : (s.points || 0) > 0 ? 'pos'
                          : (s.points || 0) < 0 ? 'neg' : 'dim'}>
                        {!s.available ? 'no data'
                          : s.directional ? s.points_label : 'sizing only'}
                      </b>
                    </div>
                  ))}
              </div>
            )}
          </React.Fragment>
        ))}
      </div>
    </Panel>
  );
}
