/**
 * Подпись свёрнутых «Ящиков и разгона» — итог одной строкой: сколько ящиков, на разгоне ли и до
 * скольких в день, сколько выключено и сколько сегодня не пишет (разведка прода 10.11.2026).
 */

import { describe, expect, it } from 'vitest';

import type { StageBox } from '../api/senders';
import { boxesSummary } from './StageBoxes';

const DONE: StageBox = {
  id: 1,
  email: 'hello@mail-a.example.test',
  domain: 'mail-a.example.test',
  enabled: true,
  warmup_day: 30,
  warmup_finished: true,
  first_letter_at: '2026-10-01T09:00:00+00:00',
  daily_cap: 20,
  allowance_today: 20,
  sent_today: 4,
  cut: false,
  refused: null,
  refused_short: null,
  next_step_on: null,
  next_step_allowance: null,
};
const WARMING: StageBox = { ...DONE, id: 2, warmup_day: 1, warmup_finished: false };

/** Подпись с обычными пробелами: неразрывные держат число у его слова. */
function said(boxes: StageBox[]): string {
  return boxesSummary(boxes).replace(/\s+/g, ' ');
}

describe('итог ящиков в подписи', () => {
  it('одна доля — без перечисления', () => {
    expect(said([DONE, DONE, DONE])).toBe('3 ящика — до 20 в день');
    expect(said([WARMING, WARMING])).toBe('2 ящика на разгоне');
    expect(said([{ ...DONE, enabled: false }])).toBe('1 ящик выключен');
  });

  it('несколько долей — через запятую, выключенный ящик не считается на разгоне', () => {
    expect(said([WARMING, DONE, DONE, { ...WARMING, enabled: false }])).toBe(
      '4 ящика: 1 на разгоне, 2 — до 20 в день, 1 выключен',
    );
  });

  it('кап у ящиков разный — «разгон окончен», а не чужое число', () => {
    expect(said([DONE, { ...DONE, daily_cap: 30 }])).toBe('2 ящика — разгон окончен');
  });

  it('кто сегодня не пишет — в конце: по этому решают, раскрывать ли', () => {
    const silent = {
      ...DONE,
      refused: 'домен исчерпан на сегодня',
      refused_short: 'домен исчерпан',
    };
    expect(said([DONE, silent, silent])).toBe('3 ящика — до 20 в день · 2 сегодня не пишут');
    // Выключенный не пишет по своей причине, она уже в строке домена.
    expect(said([DONE, { ...silent, enabled: false }])).toBe(
      '2 ящика: 1 — до 20 в день, 1 выключен',
    );
  });
});
