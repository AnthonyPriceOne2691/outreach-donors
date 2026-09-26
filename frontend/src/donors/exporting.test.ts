/**
 * Подпись кнопки выгрузки и уведомление после — говорят, что в файле.
 *
 * Замечание 26.09.2026: кнопка одна, и она синхронна с тем, что на экране:
 * всех, найденных фильтром или отмеченных. Больше потолка — не обещает всех.
 */

import { describe, expect, it } from 'vitest';

import { exportOutcome, exportPlan } from './exporting';

const LIMIT = 10_000;

/** Разряды у `Intl` — неразрывным пробелом; сравниваем по обычному. */
function plain(text: string): string {
  return text.replace(/[\u00a0\u202f]/g, ' ');
}

describe('выгрузка: подпись кнопки', () => {
  it('без фильтра и отметок — всех', () => {
    expect(exportPlan({ picked: 0, filtered: false, found: 612, limit: LIMIT })).toEqual({
      label: 'Выгрузить всех · 612',
      empty: false,
      picked: false,
    });
  });

  it('с фильтром — найденных', () => {
    expect(exportPlan({ picked: 0, filtered: true, found: 45, limit: LIMIT }).label).toBe(
      'Выгрузить найденных · 45',
    );
  });

  it('отмечено — отмеченных, независимо от фильтра', () => {
    expect(exportPlan({ picked: 3, filtered: true, found: 45, limit: LIMIT })).toEqual({
      label: 'Выгрузить отмеченных · 3',
      empty: false,
      picked: true,
    });
  });

  it('найдено больше потолка — обе цифры, а не «всех»', () => {
    expect(
      plain(exportPlan({ picked: 0, filtered: false, found: 12_345, limit: LIMIT }).label),
    ).toBe('Выгрузить 10 000 из 12 345');
  });

  it('выгружать нечего — кнопка выключена, а не приносит пустой файл', () => {
    expect(exportPlan({ picked: 0, filtered: true, found: 0, limit: LIMIT }).empty).toBe(true);
  });
});

describe('выгрузка: что сказано после', () => {
  it('всё, что просили, в файле', () => {
    expect(exportOutcome({ rows: 45, asked: 45, notDonors: null, missing: null }, false)).toBe(
      'Выгружено: 45 доноров.',
    );
    expect(exportOutcome({ rows: 3, asked: 3, notDonors: 0, missing: 0 }, true)).toBe(
      'Выгружено отмеченных: 3.',
    );
  });

  it('потолок — сказано, сколько в файле и что делать', () => {
    expect(
      plain(exportOutcome({ rows: 10_000, asked: 12_345, notDonors: null, missing: null }, false)),
    ).toBe(
      'В файле 10 000 из 12 345: больше за раз не выгружается — сузьте фильтр, чтобы выгрузить остальных.',
    );
  });

  it('отмеченные, переставшие быть донорами, — сколько и почему', () => {
    expect(exportOutcome({ rows: 1, asked: 4, notDonors: 2, missing: 1 }, true)).toBe(
      'В файле 1 из 4 отмеченных: 2 уже не доноры — решение человека сменилось; 1 больше нет в базе.',
    );
  });

  it('сервер не сказал, сколько строк, — без выдуманных чисел', () => {
    expect(exportOutcome({ rows: null, asked: null, notDonors: null, missing: null }, false)).toBe(
      'Файл выгружен.',
    );
  });
});
