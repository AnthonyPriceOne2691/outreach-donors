/**
 * Запросы вкладки «Воронка»: гипотеза и период → запрос к серверу, доли шагов словами.
 *
 * **Период — сутками человека.** «7 дней» — сегодня и шесть дней до него, с полуночи по
 * часам браузера; «свои даты» — с полуночи первого дня до полуночи после последнего. На
 * сервер уходят моменты ISO: пояса человека сервер не знает, а его полночь знает браузер.
 * Границы — полночи, а не «сейчас»: иначе ключ кэша менялся бы каждую секунду и экран
 * перечитывал бы воронку без конца.
 *
 * **Доля — со своей основой словами.** Доставлено, отказ и ответ — от отправленных; лид
 * передан — от ответивших. Основа ноль — доли нет, и это сказано, а не «0%».
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { readSalesFunnel } from '../api/sales';
import type { FunnelQuery } from '../api/sales';
import type { FunnelCounts } from '../api/salesTypes';
import { formatPercent } from '../format';

export const FUNNEL_QUERY_KEY = ['sales', 'funnel'] as const;

export type PeriodKey = 'week' | 'month' | 'all' | 'custom';

/** Период — подписи переключателя, в порядке на экране. */
export const PERIODS: Record<PeriodKey, string> = {
  week: '7 дней',
  month: '30 дней',
  all: 'Всё время',
  custom: 'Свои даты',
};

export const PERIOD_KEYS = Object.keys(PERIODS) as PeriodKey[];

/** Сколько суток назад начинается период-заготовка: сегодня — нулевые сутки. */
const PRESET_DAYS: Partial<Record<PeriodKey, number>> = { week: 6, month: 29 };

export interface FunnelFilters {
  /** Номер гипотезы; `null` — все. */
  hypothesis: number | null;
  period: PeriodKey;
  /** Свои даты — значения полей даты (`YYYY-MM-DD`); пусто — без границы. */
  from: string;
  to: string;
}

export const NO_FUNNEL_FILTERS: FunnelFilters = {
  hypothesis: null,
  period: 'all',
  from: '',
  to: '',
};

/** Полночь суток `daysAgo` назад по часам браузера. */
function midnight(now: Date, daysAgo: number): Date {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysAgo);
}

/** День поля даты → полночь этого дня, сдвинутая на `plus` суток. Не дата — `null`. */
function dayOf(value: string, plus = 0): Date | null {
  const found = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (found === null) return null;
  const day = new Date(Number(found[1]), Number(found[2]) - 1, Number(found[3]) + plus);
  return Number.isNaN(day.getTime()) ? null : day;
}

/** Почему свои даты не годятся — словами; `null` — годятся (или период не свой). */
export function periodProblem(filters: FunnelFilters): string | null {
  if (filters.period !== 'custom') return null;
  const since = dayOf(filters.from);
  const until = dayOf(filters.to, 1);
  if (filters.from !== '' && since === null) return 'Первый день — не дата.';
  if (filters.to !== '' && until === null) return 'Последний день — не дата.';
  if (since !== null && until !== null && since >= until) {
    return 'Первый день позже последнего — поменяйте даты местами.';
  }
  return null;
}

/** Фильтры → запрос к серверу. Незаданное в запрос не идёт: без него — все и всё время. */
export function funnelQuery(filters: FunnelFilters, now: Date): FunnelQuery {
  const query: FunnelQuery = {};
  if (filters.hypothesis !== null) query.hypothesis = filters.hypothesis;
  const days = PRESET_DAYS[filters.period];
  if (days !== undefined) query.since = midnight(now, days).toISOString();
  if (filters.period === 'custom') {
    const since = dayOf(filters.from);
    const until = dayOf(filters.to, 1);
    if (since !== null) query.since = since.toISOString();
    if (until !== null) query.until = until.toISOString();
  }
  return query;
}

/** Воронка под фильтром. Прежний ответ стоит, пока едет новый; негодный период не уходит. */
export function useSalesFunnel(query: FunnelQuery, enabled: boolean) {
  return useQuery({
    queryKey: [...FUNNEL_QUERY_KEY, query],
    queryFn: () => readSalesFunnel(query),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export interface StepView {
  key: keyof FunnelCounts;
  /** Подпись плитки. */
  title: string;
  /** Заголовок колонки таблицы по гипотезам — короче плитки. */
  column: string;
}

/** Шаги в порядке пути лида. */
export const STEPS: StepView[] = [
  { key: 'queued', title: 'В очереди', column: 'В очереди' },
  { key: 'sent', title: 'Отправлено', column: 'Отправлено' },
  { key: 'delivered', title: 'Доставлено', column: 'Доставлено' },
  { key: 'bounced', title: 'Отказ', column: 'Отказ' },
  { key: 'answered', title: 'Ответ', column: 'Ответ' },
  { key: 'handed_off', title: 'Лид передан', column: 'Передан' },
];

/** Основа доли шага: от чего она считается и как назвать её отсутствие. */
const BASES: Partial<Record<keyof FunnelCounts, { of: keyof FunnelCounts; words: string }>> = {
  delivered: { of: 'sent', words: 'отправленных' },
  bounced: { of: 'sent', words: 'отправленных' },
  answered: { of: 'sent', words: 'отправленных' },
  handed_off: { of: 'answered', words: 'ответивших' },
};

/** Доля шага словами: «93,6% от отправленных»; основа ноль — «нет отправленных».
 *  У шагов без доли — что считает шаг. */
export function stepHint(counts: FunnelCounts, key: keyof FunnelCounts): string {
  const base = BASES[key];
  if (base === undefined) return key === 'queued' ? 'ещё ничего не ушло' : 'лидов, не писем';
  const whole = counts[base.of];
  return whole === 0
    ? `нет ${base.words}`
    : `${formatPercent(counts[key] / whole)} от ${base.words}`;
}

/** Доля для ячейки таблицы — без слов основы: она названа над таблицей. */
export function stepShare(counts: FunnelCounts, key: keyof FunnelCounts): string | null {
  const base = BASES[key];
  if (base === undefined || counts[base.of] === 0) return null;
  return formatPercent(counts[key] / counts[base.of]);
}
