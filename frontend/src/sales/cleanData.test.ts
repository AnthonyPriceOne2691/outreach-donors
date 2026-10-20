/**
 * Итог очистки словами (`cleanLine`): лиды, за которых Hunter «ещё проверяет», — своим числом и
 * сроком, когда перепроверка пойдёт сама (разбор прогона сбора 20.10.2026: «следующая очистка
 * повторит» обещала то, чего никто не запускал). Остальные слова итога — `CleanLeads.test.tsx`.
 */

import { describe, expect, it } from 'vitest';

import { formatDateTime, formatTime } from '../format';
import { cleanLine } from './cleanData';

/** Срок — через двадцать минут от «сейчас»: сегодня (около полуночи — уже завтра, с датой). */
const SOON = new Date(Date.now() + 20 * 60_000);
const sameDay = SOON.toDateString() === new Date().toDateString();

const REPORT = {
  checked: 5,
  ready: 3,
  rejected: {},
  unconfirmed: 0,
  recheck_on: null,
  unverified: 0,
  still_checking: 2,
  again_at: SOON.toISOString(),
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

    const at = sameDay ? formatTime(REPORT.again_at) : formatDateTime(REPORT.again_at);
    expect(line).toContain(`Hunter ещё проверяет адрес — 2: перепроверка пойдёт сама около ${at}.`);
    expect(line).not.toContain('следующая очистка повторит');
  });

  it('срок не сегодня — с датой: «около 09:50» завтрашним утром читалось бы прошедшим', () => {
    const later = new Date(Date.now() + 26 * 3_600_000).toISOString();

    expect(cleanLine({ ...REPORT, again_at: later })).toContain(
      `перепроверка пойдёт сама около ${formatDateTime(later)}.`,
    );
  });

  it('таких нет — строки нет', () => {
    expect(cleanLine({ ...REPORT, still_checking: 0, again_at: null })).not.toContain('Hunter');
  });
});
