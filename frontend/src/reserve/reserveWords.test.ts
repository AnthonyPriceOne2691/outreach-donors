import { describe, expect, it } from 'vitest';

import { readReserve, writeReserve } from './reserveFilters';
import { GROUP_TITLES, placeWords, takeOutcome, topWords } from './reserveWords';

const ASKED = { place: null, share: null, top: 'us', top_share: 0.55, asked: true, partial: false };

describe('место страны прогона у домена', () => {
  it('место и доля — или почему места нет', () => {
    expect(placeWords({ ...ASKED, place: 2, share: 0.18 }, 'ca')).toBe('Канада — 2-я · 18%');
    expect(placeWords(ASKED, 'ca')).toBe('Канада не в топ-5');
    expect(placeWords({ ...ASKED, asked: false }, 'ca')).toBe('страны не спрашивали');
    expect(placeWords({ ...ASKED, top: null }, 'ca')).toBe('данных по странам нет');
    expect(placeWords({ ...ASKED, partial: true }, 'ca')).toBe(
      'Канада — не видно: одна верхняя страна',
    );
  });

  it('верхняя страна — второй строкой, если это не страна прогона', () => {
    expect(topWords(ASKED, 'ca')).toBe('верхняя — США · 55%');
    expect(topWords({ ...ASKED, place: 1 }, 'us')).toBeNull();
    expect(topWords({ ...ASKED, top: null }, 'ca')).toBeNull();
    expect(topWords(ASKED, 'US')).toBeNull();
  });
});

describe('группы причин', () => {
  it('«не проверен» из-за нехватки данных — своя группа, и она читается из адреса', () => {
    expect(GROUP_TITLES.gap).toBe('Не проверен — не хватает данных');
    expect(readReserve(new URLSearchParams('group=gap')).group).toBe('gap');
    expect(readReserve(new URLSearchParams('group=lost')).group).toBeNull();
  });
});

describe('итог «Взять на рассмотрение»', () => {
  it('взятые — по прогонам, уже лежавшие и отказы — словами', () => {
    expect(
      takeOutcome({
        taken: 2,
        runs: { '30': ['a.example'], '31': ['b.example'] },
        already: [],
        refused: {},
      }),
    ).toBe('Взято на рассмотрение: 2 домена — в очереди прогонов №30, №31');
    expect(
      takeOutcome({
        taken: 0,
        runs: {},
        already: ['a.example'],
        refused: { 'b.example': 'уже донор' },
      }),
    ).toBe('Уже были в очереди: 1. Не взяты: b.example — уже донор');
    expect(takeOutcome({ taken: 0, runs: {}, already: [], refused: {} })).toBe('Брать было нечего');
  });
});

describe('фильтры резерва в адресе', () => {
  it('туда и обратно; мусор из адреса фильтром не становится', () => {
    const filters = { run: 30, country: 'ca', group: 'region' as const, geo: 'gb', page: 2 };
    expect(readReserve(writeReserve(filters))).toEqual(filters);
    expect(readReserve(new URLSearchParams('run=abc&country=Канада&group=x&page=-1'))).toEqual({
      run: null,
      country: null,
      group: null,
      geo: null,
      page: 1,
    });
  });
});
