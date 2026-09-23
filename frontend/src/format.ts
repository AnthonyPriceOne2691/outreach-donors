/**
 * Числа, деньги и даты — одним местом на весь фронт.
 *
 * Трафик на трёх экранах был показан тремя способами («1462.8 млн»,
 * «1462826869», «267k»), деньги — с точкой, даты — то с временем, то без.
 * Каждый по отдельности читается, вместе — как три разных приложения.
 * Отсюда правило: число на экран выводится только через эти функции.
 */

const LOCALE = 'ru-RU';

const whole = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat(LOCALE, {
  notation: 'compact',
  maximumFractionDigits: 1,
});
const money = new Intl.NumberFormat(LOCALE, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function missing(value: number | string | null | undefined): boolean {
  return value === null || value === undefined || value === '' || Number.isNaN(Number(value));
}

/** Целое с разрядами: «1 462 826 869». */
export function formatNumber(value: number | string | null | undefined): string {
  return missing(value) ? '—' : whole.format(Number(value));
}

/** Кратко, где места мало: «1,5 млрд», «267 тыс.». До тысячи — как есть. */
export function formatCompact(value: number | string | null | undefined): string {
  return missing(value) ? '—' : compact.format(Number(value));
}

/** Доллары: «0,72 $». Сервер отдаёт деньги строкой, чтобы не терять копейки. */
export function formatUsd(value: number | string | null | undefined): string {
  return missing(value) ? '—' : `${money.format(Number(value))} $`;
}

/** Доля 0…1 процентами: «18%». */
export function formatShare(share: number | null | undefined): string {
  return share === null || share === undefined ? '—' : `${Math.round(share * 100)}%`;
}

/** Дата: «23.09.2026». */
export function formatDate(moment: string | null | undefined): string {
  return moment === null || moment === undefined || moment === ''
    ? '—'
    : new Date(moment).toLocaleDateString(LOCALE);
}

/** Дата и время без секунд: «23.09.2026, 14:05». */
export function formatDateTime(moment: string | null | undefined): string {
  return moment === null || moment === undefined || moment === ''
    ? '—'
    : new Date(moment).toLocaleString(LOCALE, {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
}
