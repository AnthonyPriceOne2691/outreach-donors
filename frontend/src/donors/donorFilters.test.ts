/**
 * Фильтры доноров в адресе: разбор строгий, запись та же, что у сервера.
 *
 * Адрес правят руками и присылают устаревшим. Незнакомое значение — «не
 * сужать», а не отказ сервера по-английски и не пустой экран без объяснения.
 */

import { describe, expect, it } from 'vitest';

import {
  conditionsOf,
  emptinessOf,
  NO_FILTERS,
  queryOf,
  readFilters,
  writeFilters,
} from './donorFilters';

function read(query: string) {
  return readFilters(new URLSearchParams(query));
}

/** Разряды у `Intl` — неразрывным пробелом; сравниваем по обычному. */
function plain(text: string): string {
  return text.replace(/[\u00a0\u202f]/g, ' ');
}

describe('фильтры доноров: разбор адреса', () => {
  it('трафик, страна и данные читаются из адреса', () => {
    expect(read('min_traffic=1000000000&geo=DE&freshness=stale')).toMatchObject({
      minTraffic: 1_000_000_000,
      geo: 'de',
      freshness: 'stale',
    });
  });

  it.each([
    ['min_traffic=-5', 'minTraffic'],
    ['min_traffic=1e9', 'minTraffic'],
    ['min_traffic=99999999999999', 'minTraffic'],
    ['geo=usa', 'geo'],
    ['geo=1', 'geo'],
    ['freshness=old', 'freshness'],
  ] as const)('незнакомое значение «%s» — не сужать', (query, field) => {
    expect(read(query)[field]).toBeNull();
  });

  it('запись — те же имена, что у сервера, и назад читается то же самое', () => {
    const filters = {
      ...NO_FILTERS,
      minTraffic: 2_400_000,
      geo: 'us',
      freshness: 'never' as const,
      page: 3,
    };

    const written = writeFilters(filters);

    expect(written.toString()).toBe('min_traffic=2400000&geo=us&freshness=never&page=3');
    expect(readFilters(written)).toEqual(filters);
    expect(queryOf(filters)).toEqual({ min_traffic: 2_400_000, geo: 'us', freshness: 'never' });
  });

  it('условия пустого результата — словами, числа и страны — общими форматами', () => {
    expect(
      conditionsOf({ ...NO_FILTERS, minTraffic: 1_000_000, geo: 'de', freshness: 'stale' }).map(
        plain,
      ),
    ).toEqual(['трафик не ниже 1 000 000', 'страна «Германия»', 'данные «пора обновить»']);
  });
});

describe('фильтры доноров: пустой экран', () => {
  it('доноров нет вовсе — не «база пуста», а путь туда, где их принимают', () => {
    const empty = emptinessOf(NO_FILTERS, {}, { domains: 394, runs: [24, 18] });

    expect(empty.title).toBe('Доноров пока нет.');
    expect(empty.detail).toMatch(/когда его принимает человек на рассмотрении прогона/);
    expect(empty.detail).toMatch(/Ждут решения: 394\./);
    // Туда, где ждут, — в самую свежую очередь.
    expect(empty.path).toEqual({ label: 'Рассмотреть · 394', to: '/runs/24/review' });
  });

  it('ждущих нет — ведёт к прогонам', () => {
    const empty = emptinessOf(NO_FILTERS, {}, { domains: 0, runs: [] });

    expect(empty.path).toEqual({ label: 'К прогонам', to: '/run' });
    expect(empty.detail).toMatch(/их приносит прогон/);
  });

  it('под фильтром пусто — названы условия, а путь к прогонам не нужен', () => {
    const empty = emptinessOf(
      { ...NO_FILTERS, geo: 'de' },
      { suitable: 4 },
      { domains: 0, runs: [] },
    );

    expect(empty.detail).toBe('Условия: страна «Германия». Всего доноров — 4.');
    expect(empty.path).toBeUndefined();
    expect(empty.resettable).toBe(true);
  });
});
