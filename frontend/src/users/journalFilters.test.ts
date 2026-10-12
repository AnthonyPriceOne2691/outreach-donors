/**
 * Фильтры журнала в адресе: чужой ввод разбирается строго — незнакомое «не сужает»;
 * отрезок дней — по часам человека, «по» включает весь последний день.
 */

import { describe, expect, it } from 'vitest';

import { journalParams, momentsOf, rangeProblem, readJournalFilters } from './journalFilters';

describe('фильтры журнала в адресе', () => {
  it('годное читается, негодное — «не сужать», а не отказ', () => {
    expect(
      readJournalFilters(
        new URLSearchParams('action=login&author=3&from=2026-10-05&to=2026-10-06&page=2'),
      ),
    ).toEqual({ action: 'login', author: 3, from: '2026-10-05', to: '2026-10-06', page: 2 });
    expect(
      readJournalFilters(
        new URLSearchParams('action=drop&author=x&from=2026-02-31&to=вчера&page=0'),
      ),
    ).toEqual({ action: null, author: null, from: '', to: '', page: 1 });
  });

  it('в адрес — только заданное, первая страница — без номера', () => {
    expect(
      journalParams({ action: null, author: 3, from: '', to: '2026-10-06', page: 1 }).toString(),
    ).toBe('author=3&to=2026-10-06');
  });

  it('конец раньше начала — словами; один край или равные дни — годится', () => {
    expect(rangeProblem('2026-10-06', '2026-10-05')).toBe('Раньше первого дня — поменяйте даты');
    expect(rangeProblem('2026-10-05', '2026-10-05')).toBeNull();
    expect(rangeProblem('', '2026-10-05')).toBeNull();
  });

  it('края — с начала первого дня до начала дня после последнего, по часам машины', () => {
    expect(momentsOf('2026-10-05', '2026-10-05')).toEqual({
      since: new Date(2026, 9, 5).toISOString(),
      until: new Date(2026, 9, 6).toISOString(),
    });
    expect(momentsOf('', '')).toEqual({ since: null, until: null });
  });
});
