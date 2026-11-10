/**
 * Запросы вкладки «Рассылка»: части адресов продаж (`GET /sales/mailing`) и прогноз рассылки этапа
 * продаж (`GET /letters/forecast`) — обе раз в минуту; дни, часы, ступени ящиков — словами.
 *
 * **Экран своего счёта не ведёт.** Части адресов, срок очереди, мощность, план и факт по дням и
 * ступени ящиков считает сервер — теми же функциями, что сборка, пачка и выбор ящика. Здесь —
 * только как их назвать: день — «пн 26.10», часы — с десятой, ступень — «день 3: до 10 из 20».
 *
 * **Раз в минуту** (`MAILING_REFRESH_MS`): письма уходят весь день, и прогноз, прочитанный утром,
 * к обеду врал бы. Чаще незачем — проход автоотправки идёт раз в минуту.
 *
 * **Прогноз — один ключ кэша у вкладки и у строки «Очереди писем»** (`useSalesForecast`): срок
 * очереди на двух вкладках — один ответ, а не два запроса, разошедшихся на минуту.
 *
 * **Дни — по UTC, как у сервера**: день плана — сутки дневного лимита ящиков. Экран не переводит
 * его в часы браузера: на стыке суток человек в другом поясе видел бы не тот день (то же правило —
 * у недельного отчёта, `weeklyData.dayWords`).
 */

import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';

import { request } from '../api/client';
import type { MailingForecastView, SalesMailingView } from './mailingTypes';

export { dayLabel } from '../format';

/** Обе ручки вкладки перечитываются раз в минуту. */
export const MAILING_REFRESH_MS = 60_000;

/** План — сегодня и шесть дней вперёд; факт — семь дней назад, без сегодня. */
export const PLAN_DAYS = 7;
export const PAST_DAYS = 7;

export const MAILING_QUERY_KEY = ['sales', 'mailing'] as const;
export const FORECAST_QUERY_KEY = ['letters', 'forecast', 'sales', PLAN_DAYS, PAST_DAYS] as const;

export function readSalesMailing(): Promise<SalesMailingView> {
  return request<SalesMailingView>('/sales/mailing');
}

export function readSalesForecast(): Promise<MailingForecastView> {
  return request<MailingForecastView>(
    `/letters/forecast?stage=sales&days=${PLAN_DAYS}&back=${PAST_DAYS}`,
  );
}

/** Части адресов и строки режима — раз в минуту. */
export function useSalesMailing(): UseQueryResult<SalesMailingView> {
  return useQuery({
    queryKey: MAILING_QUERY_KEY,
    queryFn: readSalesMailing,
    refetchInterval: MAILING_REFRESH_MS,
  });
}

/** Прогноз рассылки продаж — раз в минуту; тот же ключ у вкладки и у строки очереди. */
export function useSalesForecast(): UseQueryResult<MailingForecastView> {
  return useQuery({
    queryKey: FORECAST_QUERY_KEY,
    queryFn: readSalesForecast,
    refetchInterval: MAILING_REFRESH_MS,
  });
}

/** Когда пришли числа, которые на экране: у двух ручек — старшее из двух, чтобы «обновлено» не
 *  обещало свежести, которой у одной из них нет. Ничего не пришло — `null`. */
export function updatedAt(...answers: { data: unknown; dataUpdatedAt: number }[]): number | null {
  const moments = answers
    .filter((answer) => answer.data !== undefined)
    .map((answer) => answer.dataUpdatedAt);
  return moments.length === 0 ? null : Math.min(...moments);
}

const tenths = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });

/** Часы до последнего письма очереди — с десятой, как их считает сервер: «≈ 26,5 ч». */
/** Где слова сервера делятся на голову и подробности: голова — до первого из них. */
const HEAD_CUTS = [': ', ' — ', '; '];

/** Голова слов сервера — до первого «: », « — » или «; »; без них — слова целиком. Экран их не
 *  сочиняет, а делит: голова — на виду, целиком — в «i» рядом (причина дня плана, строка пачки). */
export function reasonHead(words: string): string {
  const cuts = HEAD_CUTS.map((cut) => words.indexOf(cut)).filter((at) => at > 0);
  return cuts.length === 0 ? words : words.slice(0, Math.min(...cuts));
}

export function hoursLabel(hours: number): string {
  return `≈ ${tenths.format(hours)} ч`;
}
