/**
 * Итог очистки словами (`cleanLine`): лиды, за которых Hunter «ещё проверяет», — своим числом и
 * сроком, когда перепроверка пойдёт сама (разбор прогона сбора 20.10.2026: «следующая очистка
 * повторит» обещала то, чего никто не запускал). Остальные слова итога — `CleanLeads.test.tsx`.
 */

import { describe, expect, it } from 'vitest';

import { formatTime } from '../format';
import { cleanLine } from './cleanData';

const REPORT = {
  checked: 5,
  ready: 3,
  rejected: {},
  unconfirmed: 0,
  recheck_on: null,
  unverified: 0,
  still_checking: 2,
  again_at: '2026-10-20T09:20:00+00:00',
  awaiting_holder: 0,
  mx_unknown: 0,
  verified: 3,
  paid_units: 3,
  verifier: 'hunter',
  stopped: null,
};

describe('итог очистки: Hunter ещё проверяет', () => {
  it('число и срок перепроверки — словами, без «следующая очистка повторит»', () => {
    const line = cleanLine(REPORT);

    expect(line).toContain(
      `Hunter ещё проверяет адрес — 2: перепроверка пойдёт сама около ${formatTime(REPORT.again_at)}.`,
    );
    expect(line).not.toContain('следующая очистка повторит');
  });

  it('таких нет — строки нет', () => {
    expect(cleanLine({ ...REPORT, still_checking: 0, again_at: null })).not.toContain('Hunter');
  });
});
