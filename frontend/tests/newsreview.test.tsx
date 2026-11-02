import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import SmartMoveNewsReviewPage from '../src/pages/SmartMoveNewsReviewPage';
import { HUMAN_LABELS, canSubmit, classTone, gateText, humanStateText, isDisputed, labelsGiven, myLabel, progressText, reviewability } from '../src/pages/newsReviewLogic';
import queue from './fixtures/news_queue.json';

const g = globalThis as any;
const ctx: any = {};
const page = (state: any) => {
  g.__apiState = state;
  return renderToStaticMarkup(<SmartMoveNewsReviewPage ctx={ctx} />);
};
const row = (cls: string) => (queue as any).rows.find((r: any) => r.audit_class === cls);

test('the queue shows saved headlines with company, ticker, event, timestamps, source credibility and age', () => {
  const html = page({ queue: { data: queue } });
  for (const s of ['ACME ROBOTICS INC', 'ACME', '2026-11-04', 'quarter 2026-09-30', 'published', 'retrieved', 'trusted wire', 'unknown source', '30 h before the cutoff']) assert.ok(html.includes(s), s);
});

test('all six automated classes are shown and are separate from the human review column', () => {
  const html = page({ queue: { data: queue } });
  for (const c of ['VALID', 'STALE', 'DUPLICATE', 'IRRELEVANT', 'UNVERIFIED', 'INVALID']) assert.ok(html.includes(c), c);
  assert.match(html, /Automated classification/);
  assert.match(html, /Human review/);
  // a stale / duplicate / invalid headline offers no label buttons
  assert.match(html, /Not reviewed: automatically classed STALE/);
  assert.match(html, /Not reviewed: automatically classed DUPLICATE/);
  assert.match(html, /Not reviewed: automatically classed INVALID/);
});

test('reviewable headlines offer every human label; disputes are flagged with both reviewers shown', () => {
  const html = page({ queue: { data: queue } });
  for (const l of HUMAN_LABELS) assert.ok(html.includes(l.replace('_', ' ').toLowerCase()), l);
  assert.match(html, /disputed: reviewers disagree/);
  assert.match(html, /ann: positive/);
  assert.match(html, /bob: negative/);
  assert.match(html, /sm-q-disputed/);
});

test('review progress and the validation gate are shown, and news guidance is never described as enabled', () => {
  const html = page({ queue: { data: queue } });
  assert.match(html, /66\.7%/);
  assert.match(html, /2 of 3 reviewed/);
  assert.match(html, /news guidance stays DISABLED/);
  assert.match(html, /News guidance is DISABLED/);
  assert.doesNotMatch(html, /guidance (is )?enabled/i);
  assert.doesNotMatch(html, /recommend(ation)?s? (buy|sell)/i);
});

test('loading, failure, missing queue and unreadable queue have visible states', () => {
  assert.match(page({ queue: { initialLoading: true } }), /Loading the review queue/);
  const failed = page({ queue: { error: 'SERVICE_UNAVAILABLE' } });
  assert.match(failed, /could not be loaded: SERVICE_UNAVAILABLE/);
  assert.match(failed, /Retry/);
  assert.match(page({ queue: { data: { status: 'NO_QUEUE', detail: 'The review queue has not been built yet.' } } }), /No review queue yet/);
  assert.match(page({ queue: { data: { status: 'UNREADABLE', detail: 'bad' } } }), /Review queue unreadable/);
});

test('an empty filtered queue says so', () => {
  const html = page({ queue: { data: { ...(queue as any), rows: [], total: 0 } } });
  assert.match(html, /No headlines match these filters/);
});

test('review rules: only VALID / UNVERIFIED / IRRELEVANT are open; a reviewer name and a known label are required', () => {
  assert.equal(reviewability(row('VALID')).open, true);
  assert.equal(reviewability(row('STALE')).open, false);
  assert.equal(canSubmit('', 'POSITIVE').ok, false);
  assert.equal(canSubmit('ann', 'BULLISH').ok, false);
  assert.equal(canSubmit('ann', 'UNSURE').ok, true);
});

test('human state text keeps human labels separate from automated classes', () => {
  assert.equal(humanStateText(row('STALE')), 'not reviewable');
  assert.equal(humanStateText(row('UNVERIFIED')), 'not yet reviewed');
  assert.equal(humanStateText(row('VALID')), 'disputed: reviewers disagree');
  assert.equal(isDisputed(row('VALID')), true);
  assert.deepEqual(labelsGiven(row('VALID')), [['ann', 'POSITIVE'], ['bob', 'NEGATIVE']]);
  assert.equal(myLabel(row('VALID'), 'ann'), 'POSITIVE');
  assert.equal(myLabel(row('VALID'), 'zed'), null);
  assert.equal(classTone('VALID'), 'green');
  assert.equal(classTone('INVALID'), 'red');
});

test('progress and gate wording never claim guidance is enabled', () => {
  assert.match(progressText((queue as any).progress), /2 of 3 reviewed/);
  assert.match(gateText((queue as any).gate), /stays DISABLED/);
  assert.match(gateText({ labelled: 999, requirements: { a: true, b: true }, may_be_considered_for_scoring: true, enabled: false }), /stays DISABLED.*not enabled/);
  assert.equal(progressText(null), 'no progress available');
});
