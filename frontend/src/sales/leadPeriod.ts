/**
 * Период добавления на вкладке «emails»: «с» и «по» — днями человека, оба дня включительно.
 *
 * **Сутки — те же, что у своих дат воронки**, и считает их функция воронки (`funnelQuery`),
 * а не копия: «с» — с полуночи первого дня по часам браузера, «по» — до полуночи после
 * последнего. На сервер уходят моменты ISO (`added_since`, `added_until`): пояса человека
 * сервер не знает, а его полночь знает браузер.
 *
 * **В адресе — дни, своими ключами** (`added_from`, `added_to`), а не воронкины `from` и `to`:
 * у каждой вкладки свои фильтры, и смена вкладки период не переносит. Адрес — чужой ввод:
 * не настоящий день («2026-02-30», «1e3») — «не сужать», как у остальных фильтров.
 *
 * **«С» позже «по» — словами под полями, без запроса**: такой период пуст по определению,
 * а отказ сервера читался бы сбоем.
 */

import type { LeadsQuery } from '../api/sales';
import { formatDate } from '../format';
import { funnelQuery } from './funnelData';

/** Ключи периода в адресе экрана — свои, не воронкины. */
export const ADDED_KEYS = { from: 'added_from', to: 'added_to' } as const;

const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Полночь дня поля даты по часам браузера — или `null`, если это не настоящий день: «2026-02-30»
 *  `Date` молча перенёс бы на 2 марта, а годы до сотого — в двадцатый век. */
function midnightOf(text: string): Date | null {
  const found = DAY.exec(text);
  if (found === null) return null;
  const [year, month, day] = [Number(found[1]), Number(found[2]), Number(found[3])];
  const moment = new Date(year, month - 1, day);
  const same =
    moment.getFullYear() === year && moment.getMonth() === month - 1 && moment.getDate() === day;
  return same ? moment : null;
}

/** День из адреса или поля: настоящий день `ГГГГ-ММ-ДД` — как есть, иначе пусто («не сужать»). */
export function addedDayOf(raw: string | null): string {
  return raw !== null && midnightOf(raw) !== null ? raw : '';
}

/** Набранное в поле — то же, что в адресе: недонабранный день адрес не трогает. */
export function sameAddedDay(draft: string, committed: string): boolean {
  return addedDayOf(draft) === committed;
}

/** Почему период не фильтр — словами под полями; `null` — годится. */
export function addedProblem(from: string, to: string): string | null {
  return from !== '' && to !== '' && from > to ? '«С» позже «по» — поменяйте даты местами' : null;
}

/** Период → границы запроса тем же правилом, что свои даты воронки. Пустой день — без границы. */
export function addedQuery(
  from: string,
  to: string,
): Pick<LeadsQuery, 'added_since' | 'added_until'> {
  const { since, until } = funnelQuery(
    { hypothesis: null, period: 'custom', from, to },
    new Date(),
  );
  return {
    ...(since !== undefined ? { added_since: since } : {}),
    ...(until !== undefined ? { added_until: until } : {}),
  };
}

/** День словами экрана — тем же форматом, что даты в таблице: «01.10.2026». */
function dayWords(text: string): string {
  return formatDate(midnightOf(text)?.toISOString());
}

/** Период словами — условие пустой таблицы: «добавлены с 01.10.2026 по 15.10.2026». */
export function addedWords(from: string, to: string): string | null {
  const bounds = [
    ...(from !== '' ? [`с ${dayWords(from)}`] : []),
    ...(to !== '' ? [`по ${dayWords(to)}`] : []),
  ];
  return bounds.length === 0 ? null : `добавлены ${bounds.join(' ')}`;
}
