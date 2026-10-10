/**
 * Место решённой строки «Отбора» (проверка QA 10.10.2026): правило порядка
 * отдельно от экрана — какие строки и в каком порядке встают после решения.
 */

import { describe, expect, it } from 'vitest';

import type { SelectionCard } from '../api/types';
import { arrange, blankHold, remember } from './useDecisions';

function row(id: number, extra: Partial<SelectionCard> = {}): SelectionCard {
  return {
    domain_id: id,
    host: `site-${id}.test`,
    tab: 'accepted',
    donor_id: null,
    status: 'suitable',
    reject_reason: null,
    dr: 50 - id,
    org_traffic: null,
    machine: {
      intent: null,
      recommendation: null,
      decided_by: null,
      quote: null,
      reason: null,
      source_url: null,
      home_shop: [],
      home_reached: null,
      judged_at: null,
    },
    human: { intent: null, note: null, decided_at: null },
    seller: { answer: null, answered_at: null, price: null, currency: null },
    disagrees: false,
    ...extra,
  };
}

const VIEW = '["selection","accepted"]';

describe('место решённой строки', () => {
  it('решений не было — строки как пришли', () => {
    const fresh = [row(1), row(2)];

    const arranged = arrange(fresh, blankHold(VIEW));

    expect(arranged.rows).toBe(fresh);
    expect(arranged.gone.size).toBe(0);
  });

  it('ушедшая — на своём месте, пришедшая с соседней страницы — в конце', () => {
    const decided = row(2, { tab: 'rejected', human: { ...row(2).human, intent: 'sells_own' } });
    const hold = remember(
      blankHold(VIEW),
      { row: row(2), intent: 'sells_own', view: VIEW, order: [1, 2, 3] },
      decided,
    );

    const arranged = arrange([row(1), row(3), row(4)], hold);

    expect(arranged.rows.map((one) => one.domain_id)).toEqual([1, 2, 3, 4]);
    expect(arranged.rows[1]).toBe(decided);
    expect([...arranged.gone]).toEqual([2]);
  });

  it('«Вернуть» помнит решение до первого нажатия, а не до последнего', () => {
    const asked = { row: row(2), intent: 'sells_own' as const, view: VIEW, order: [1, 2] };
    const first = remember(blankHold(VIEW), asked, row(2, { tab: 'rejected' }));
    const second = remember(
      first,
      { ...asked, row: row(2, { human: { ...row(2).human, intent: 'sells_own' } }) },
      row(2, { tab: 'rejected', human: { ...row(2).human, intent: 'non_commercial' } }),
    );

    expect(second.held.get(2)?.was).toBeNull();
  });

  it('решение с прежнего вида не ложится на новый', () => {
    const before = remember(
      blankHold('другой вид'),
      { row: row(9), intent: 'sells_own', view: 'другой вид', order: [9] },
      row(9, { tab: 'rejected' }),
    );

    const now = remember(
      before,
      { row: row(2), intent: 'sells_own', view: VIEW, order: [1, 2] },
      row(2, { tab: 'rejected' }),
    );

    expect([...now.held.keys()]).toEqual([2]);
    expect(now.view).toBe(VIEW);
  });
});
