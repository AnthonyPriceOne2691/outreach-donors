/**
 * Строка доставки письма (`deliveryLine.ts`): что с письмом, причина словами сервера, попытки
 * и часы. Часы — по часам браузера: моменты собираются местным временем, и тест читается так же
 * в Москве и в CI на UTC.
 */

import { describe, expect, it } from 'vitest';

import type { LetterDeferral } from '../api/delivery';
import { attemptsLine, clockOf, deliveryLine } from './deliveryLine';

/** Местное время 29.10.2026 — строкой ISO, как его отдаёт сервер. */
const at = (hours: number, minutes: number, day = 29) =>
  new Date(2026, 9, day, hours, minutes).toISOString();

const NOW = new Date(2026, 9, 29, 16, 30);

const UNREACHABLE: LetterDeferral = {
  kind: 'unreachable',
  words: 'сервер получателя не отвечает (порт 25, тайм-аут)',
  attempts: 14,
  since: at(10, 26),
  last: at(16, 22),
};

describe('попытки и часы', () => {
  it('сегодняшние — часами, другого дня — с числом', () => {
    expect(clockOf(at(10, 26), NOW)).toBe('10:26');
    expect(clockOf(at(10, 26, 28), NOW)).toBe('28.10, 10:26');
  });

  it('много попыток — с первой и последней', () => {
    expect(attemptsLine(UNREACHABLE, NOW)).toBe('14 попыток с 10:26, последняя 16:22');
    expect(attemptsLine({ ...UNREACHABLE, attempts: 3 }, NOW)).toBe(
      '3 попытки с 10:26, последняя 16:22',
    );
    expect(attemptsLine({ ...UNREACHABLE, attempts: 21 }, NOW)).toBe(
      '21 попытка с 10:26, последняя 16:22',
    );
  });

  it('одна попытка — её часом', () => {
    expect(attemptsLine({ ...UNREACHABLE, attempts: 1, last: UNREACHABLE.since }, NOW)).toBe(
      'попытка в 10:26',
    );
  });
});

describe('строка доставки', () => {
  it('ушло и откладывается — случай письма №404', () => {
    expect(deliveryLine({ status: 'sent', deferral: UNREACHABLE }, NOW)).toEqual({
      tone: 'waiting',
      text:
        'Доставка откладывается: сервер получателя не отвечает (порт 25, тайм-аут) · ' +
        '14 попыток с 10:26, последняя 16:22',
    });
  });

  it('не доставлено — словами отказа', () => {
    const refusal = 'ящика получателя нет или он отключён (5.1.1)';

    expect(deliveryLine({ status: 'bounced', refusal }, NOW)).toEqual({
      tone: 'refused',
      text: 'Не доставлено: ящика получателя нет или он отключён (5.1.1)',
    });
  });

  it('платформа сдалась после отсрочек — отказ и сколько она повторяла', () => {
    const line = deliveryLine(
      { status: 'bounced', refusal: UNREACHABLE.words, deferral: UNREACHABLE },
      NOW,
    );

    expect(line?.text).toBe(
      'Не доставлено: сервер получателя не отвечает (порт 25, тайм-аут) · платформа перестала ' +
        'повторять: 14 попыток с 10:26, последняя 16:22',
    );
  });

  it('доставлено после отсрочек — история остаётся', () => {
    const line = deliveryLine(
      { status: 'delivered', deferral: { ...UNREACHABLE, attempts: 3 } },
      NOW,
    );

    expect(line).toEqual({
      tone: 'delivered',
      text:
        'До доставки откладывалась: сервер получателя не отвечает (порт 25, тайм-аут) · ' +
        '3 попытки с 10:26, последняя 16:22',
    });
  });

  it('сказать нечего — строки нет', () => {
    expect(deliveryLine({ status: 'delivered' }, NOW)).toBeNull();
    expect(deliveryLine({ status: 'sent', deferral: null, refusal: null }, NOW)).toBeNull();
    expect(deliveryLine({ status: 'queued', deferral: UNREACHABLE }, NOW)).toBeNull();
    // Сервер до выкатки слов отказа не отдавал — галочки и значок говорят сами.
    expect(deliveryLine({ status: 'bounced' }, NOW)).toBeNull();
  });
});
