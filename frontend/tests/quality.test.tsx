import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import SmartMoveQualityPage, { EventDetail, NewsPanel, OutcomesPanel } from '../src/pages/SmartMoveQualityPage';
import bundle from './fixtures/synthetic_quality.json';

const g = globalThis as any;
const ctx: any = {};
const summaryOf = (b: any, extra: any = {}) => ({ status: 'OK', schema: b.schema, generated_at: b.generated_at, as_of: b.as_of, source: b.source, synthetic: b.synthetic, label: b.label, notes: b.notes, summary: b.summary, accuracy: b.accuracy, read_only: true, ...extra });
const page = (state: any) => {
  g.__apiState = state;
  return renderToStaticMarkup(<MemoryRouter><SmartMoveQualityPage ctx={ctx} /></MemoryRouter>);
};
const ev = (sym: string) => (bundle as any).events.find((e: any) => e.symbol === sym);

test('synthetic data is always labelled and never presented as live', () => {
  const html = page({ summary: { data: summaryOf(bundle) }, events: { data: { status: 'OK', rows: (bundle as any).events, total: 7, synthetic: true } } });
  assert.match(html, /SYNTHETIC EXAMPLE DATA, not market data/);
  assert.doesNotMatch(html, /Saved evidence, not live signals/);
  assert.match(html, /Every symbol, price and headline here is invented/);
});

test('a real (non-synthetic) report says saved evidence, not live signals, and has no synthetic banner', () => {
  const real = { ...(bundle as any), synthetic: false, source: 'SAVED_EVIDENCE', label: 'Saved evidence - not live signals', generated_at: new Date().toISOString() };
  const html = page({ summary: { data: summaryOf(real) }, events: { data: { status: 'OK', rows: [], total: 0 } } });
  assert.match(html, /Saved evidence, not live signals/);
  assert.doesNotMatch(html, /SYNTHETIC EXAMPLE DATA/);
});

test('an old real report warns that it is stale', () => {
  const old = { ...(bundle as any), synthetic: false, source: 'SAVED_EVIDENCE', generated_at: new Date(Date.now() - 3 * 86400_000).toISOString() };
  assert.match(page({ summary: { data: summaryOf(old) }, events: { data: { status: 'OK', rows: [], total: 0 } } }), /built 3 days old|3 days old/);
});

test('earnings events show date, session, nominal release time, calendar status and fiscal quarter', () => {
  const html = page({ summary: { data: summaryOf(bundle) }, events: { data: { status: 'OK', rows: (bundle as any).events, total: 7 } } });
  for (const s of ['SYN1', 'ALPHA SYNTHETIC CORP', '2026-11-12', 'AMC', 'release nominal', 'vendor confirmed', 'estimated', '2026-09-30']) assert.ok(html.includes(s), s);
  assert.doesNotMatch(html, /release verified/);
});

test('a missing fiscal quarter is shown as quarter unknown, not as a quarter', () => {
  const html = page({ summary: { data: summaryOf(bundle) }, events: { data: { status: 'OK', rows: [ev('SYN2')], total: 1 } } });
  assert.match(html, /quarter unknown/);
  assert.doesNotMatch(html, /source saved/);
});

test('the evidence matrix distinguishes verified, unverified, missing and ineligible', () => {
  const html = page({ summary: { data: summaryOf(bundle) }, events: { data: { status: 'OK', rows: (bundle as any).events, total: 7 } } });
  for (const c of ['green', 'amber', 'gray', 'red']) assert.ok(html.includes(`sm-q-cell ${c}`), c);
  assert.match(html, /Missing or unverified evidence is never a positive or negative signal/);
});

test('snapshot A/B: revision evidence is shown when paired and an explicit unavailable message otherwise', () => {
  const paired = renderToStaticMarkup(<EventDetail open={ev('SYN1')} synthetic={true} onClose={() => {}} />);
  assert.match(paired, /data-testid="revision-evidence"/);
  assert.match(paired, /EPS revision 10(\.0)?%/);
  const none = renderToStaticMarkup(<EventDetail open={ev('SYN2')} synthetic={true} onClose={() => {}} />);
  assert.match(none, /data-testid="revision-unavailable"/);
  assert.doesNotMatch(none, /EPS revision/);
});

test('the event detail shows capture status, quarter source, release time and review status', () => {
  const html = renderToStaticMarkup(<EventDetail open={ev('SYN1')} synthetic={true} onClose={() => {}} />);
  for (const s of ['captured admissible', 'ending_fiscal_quarter', 'known before the cutoff', 'NOMINAL (not verified)', 'SYNTHETIC EXAMPLE, not market data', 'Human review']) assert.ok(html.includes(s), s);
});

test('verified and unverified outcomes are kept apart and a prediction without an admissible capture does not count', () => {
  const html = renderToStaticMarkup(<OutcomesPanel s={summaryOf(bundle)} />);
  assert.match(html, /verified: 2/);
  assert.match(html, /unverified:quarantined corporate action: 1/);
  assert.match(html, /prediction not admissible:captured after cutoff/);
  assert.match(html, /outcome not verified:quarantined corporate action/);
});

test('accuracy figures come only from the report: one comparable pair shows its sample size and "not met", never a claim', () => {
  const html = renderToStaticMarkup(<OutcomesPanel s={summaryOf(bundle)} />);
  assert.match(html, /not met \(1 of 100, 1 of 8 weeks\)/);
  assert.match(html, /NO ACCURACY CLAIM/);
  assert.doesNotMatch(html, /claim allowed|accuracy claim is allowed/i);
});

test('with no comparable pairs no hit rate or percentage is displayed', () => {
  const none = { ...summaryOf(bundle), accuracy: { variants: {}, exclusions: {}, outcomes_by_verification: {}, claim_allowed: false, claim_text: 'NO ACCURACY CLAIM: sample too small or no verified comparable pairs', flat_band_pct: 0.5 } };
  const html = renderToStaticMarkup(<OutcomesPanel s={none} />);
  assert.match(html, /No comparable pairs yet/);
  assert.doesNotMatch(html, /hit rate|0\.\d{3}|\d+(\.\d+)?%<|95% interval/i);
});

test('news panel shows the six classes, the disabled-guidance notice and a link to review', () => {
  const html = renderToStaticMarkup(<MemoryRouter><NewsPanel s={summaryOf(bundle)} /></MemoryRouter>);
  for (const c of ['VALID', 'STALE', 'DUPLICATE', 'IRRELEVANT', 'UNVERIFIED', 'INVALID']) assert.ok(html.includes(c), c);
  assert.match(html, /disabled/);
  assert.match(html, /href="\/smart-move\/news-review"/);
});

test('loading, API failure, missing report and unreadable report each have a visible state', () => {
  assert.match(page({ summary: { initialLoading: true }, events: {} }), /Loading the evidence-quality report/);
  const failed = page({ summary: { error: 'SERVICE_UNAVAILABLE' }, events: {} });
  assert.match(failed, /could not be loaded: SERVICE_UNAVAILABLE/);
  assert.match(failed, /Retry/);
  assert.match(failed, /role="alert"/);
  const none = page({ summary: { data: { status: 'NO_REPORT', detail: 'No evidence-quality report is configured.' } }, events: {} });
  assert.match(none, /No evidence-quality report yet/);
  assert.doesNotMatch(none, /Events<\/div>/);
  const bad = page({ summary: { data: { status: 'UNREADABLE', detail: 'The report could not be read.' } }, events: {} });
  assert.match(bad, /Report unreadable/);
});

test('an empty event list says so instead of rendering a blank table', () => {
  const html = page({ summary: { data: summaryOf(bundle) }, events: { data: { status: 'OK', rows: [], total: 0 } } });
  assert.match(html, /No events match these filters/);
});

test('an events-request failure is shown without hiding the summary', () => {
  const html = page({ summary: { data: summaryOf(bundle) }, events: { error: 'HTTP_500' } });
  assert.match(html, /HTTP_500/);
  assert.match(html, /Prediction-eligible/);
});
