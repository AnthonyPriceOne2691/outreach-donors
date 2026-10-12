/**
 * Слова цены и решения человека в таблице доноров (сверка ТЗ 12.10.2026).
 *
 * Отдельным файлом: `api/labels.ts` упёрся в свой потолок. Решение словами строки —
 * те же слова, что у выгрузки (`donors/wording.REVIEW_TITLES`): тест сверяет их
 * построчно (`tests/test_donor_wording.py`).
 */

import type { DonorSort, PriceState, ReviewScope } from '../api/donorPrice';

/** Решение человека в строке таблицы и в выгрузке. */
export const DONOR_REVIEWS: Record<
  Exclude<ReviewScope, 'any'>,
  { title: string; color: string }
> = {
  accepted: { title: 'принят', color: 'green' },
  rejected: { title: 'отклонён', color: 'red' },
  undecided: { title: 'не решали', color: 'gray' },
};

/** Фильтр «Решение» — кто на экране. Без фильтра — доноры, как было (решение 26.09.2026);
 *  «все проверенные» — таблица ТЗ «со всеми сайтами и причиной отсева». `empty` — что
 *  сказать, когда записей с таким решением нет вовсе. */
export const REVIEW_SCOPES: Record<ReviewScope, { title: string; empty: string }> = {
  accepted: { title: 'доноры', empty: 'Доноров пока нет.' },
  rejected: { title: 'отклонённые', empty: 'Отклонённых нет.' },
  undecided: { title: 'без решения', empty: 'Без решения человека записей нет.' },
  any: { title: 'все проверенные', empty: 'Проверенных сайтов в базе нет.' },
};

/** Фильтр под «Ценой». Слова короткие — колонка узкая; полностью они звучат
 *  в условиях пустого результата (`conditionsOf`). */
export const PRICE_STATES: Record<PriceState, { title: string; said: string }> = {
  priced: { title: 'с ценой', said: 'с ценой' },
  fresh: { title: 'свежая', said: 'цена свежая' },
  stale: { title: 'устарела', said: 'цена устарела' },
  none: { title: 'без цены', said: 'без цены' },
};

/** Порядок строк. Пусто — в конце при любом направлении. */
export const DONOR_SORTS: Record<DonorSort, string> = {
  dr: 'по DR',
  traffic: 'по трафику',
  price: 'сначала дешёвые',
  price_desc: 'сначала дорогие',
  priced_new: 'цена — свежие',
  priced_old: 'цена — давние',
};
