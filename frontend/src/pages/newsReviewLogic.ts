/* Pure helpers for the news review screen (no React, no network), so the review rules are testable without a browser. */

export const HUMAN_LABELS = ['POSITIVE', 'NEGATIVE', 'NEUTRAL', 'NOT_RELEVANT', 'UNSURE'] as const;
export type HumanLabel = (typeof HUMAN_LABELS)[number];
export const AUDIT_CLASSES = ['VALID', 'STALE', 'DUPLICATE', 'IRRELEVANT', 'UNVERIFIED', 'INVALID'] as const;

export interface ReviewItem {
  item_id: string;
  reviewable: boolean;
  audit_class: string;
  human_labels?: Record<string, string>;
  human_state?: string;
  human_consensus?: string | null;
}

export function classTone(c: string): 'green' | 'amber' | 'gray' | 'red' | 'blue' {
  return c === 'VALID' ? 'green' : c === 'UNVERIFIED' || c === 'STALE' ? 'amber' : c === 'INVALID' || c === 'IRRELEVANT' ? 'red' : 'gray';
}

/** Why an item is (or is not) open to a human label. Only VALID / UNVERIFIED / IRRELEVANT headlines are reviewed. */
export function reviewability(item: ReviewItem): { open: boolean; reason: string } {
  return item.reviewable
    ? { open: true, reason: 'Open for review' }
    : { open: false, reason: `Not reviewed: automatically classed ${item.audit_class}` };
}

/** Human-state wording. Automated class and human label are separate facts and are never merged into one value. */
export function humanStateText(item: ReviewItem): string {
  if (!item.reviewable) return 'not reviewable';
  switch (item.human_state) {
    case 'LABELLED': return `labelled ${String(item.human_consensus || '').toLowerCase().replace('_', ' ')}`;
    case 'DISPUTED': return 'disputed: reviewers disagree';
    case 'UNSURE': return 'reviewer unsure';
    default: return 'not yet reviewed';
  }
}

export function isDisputed(item: ReviewItem): boolean {
  return item.human_state === 'DISPUTED';
}

/** Labels already given, as [reviewer, label] pairs sorted by reviewer. */
export function labelsGiven(item: ReviewItem): [string, string][] {
  return Object.entries(item.human_labels || {}).sort((a, b) => a[0].localeCompare(b[0]));
}

/** The label a given reviewer currently has on the item (their newest), or null. */
export function myLabel(item: ReviewItem, reviewer: string): string | null {
  const r = reviewer.trim();
  return r && item.human_labels ? item.human_labels[r] ?? null : null;
}

export function canSubmit(reviewer: string, label: string): { ok: boolean; reason?: string } {
  if (!reviewer.trim()) return { ok: false, reason: 'Enter your reviewer name first.' };
  if (!(HUMAN_LABELS as readonly string[]).includes(label)) return { ok: false, reason: 'Unknown label.' };
  return { ok: true };
}

export interface Progress { reviewable: number; unlabelled: number; labelled: number; disputed: number; unsure: number; percent_reviewed: number | null }

export function progressText(p: Progress | undefined | null): string {
  if (!p) return 'no progress available';
  if (!p.reviewable) return 'no reviewable headlines';
  return `${p.reviewable - p.unlabelled} of ${p.reviewable} reviewed (${p.percent_reviewed ?? 0}%) · ${p.labelled} agreed · ${p.disputed} disputed · ${p.unsure} unsure`;
}

export interface Gate { labelled: number; requirements: Record<string, boolean>; may_be_considered_for_scoring: boolean; enabled: boolean }

/** Validation-gate wording. News guidance is never described as enabled, whatever the gate numbers say. */
export function gateText(g: Gate | undefined | null): string {
  if (!g) return 'Validation gate unavailable.';
  const met = Object.values(g.requirements || {}).filter(Boolean).length;
  const total = Object.keys(g.requirements || {}).length;
  return `${g.labelled} independently labelled · ${met} of ${total} requirements met · news guidance stays DISABLED${g.may_be_considered_for_scoring ? ' (eligible for the owner to consider; not enabled)' : ''}`;
}
