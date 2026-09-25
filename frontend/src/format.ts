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

/** Процент с точностью до `digits` знаков, без хвостовых нулей: «25,4», «19». */
const percents = new Map<number, Intl.NumberFormat>();

function percentFormat(digits: number): Intl.NumberFormat {
  const known = percents.get(digits);
  if (known !== undefined) return known;
  const made = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: digits });
  percents.set(digits, made);
  return made;
}

/** Знаки валют, которые узнают без подписи. Сервер приводит валюту к коду
 *  (`replies/money.py`); остальные коды — крипта, злотый, франк — пишутся
 *  кодом: «300,00 USDT» понятнее, чем значок, который не все узнают. */
const CURRENCY_SIGNS: Record<string, string> = {
  USD: '$',
  EUR: '€',
  GBP: '£',
  RUB: '₽',
};

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

/**
 * Деньги с валютой: «250,00 €», «1 250,50 $», «300,00 USDT».
 *
 * Одна функция на все деньги сервиса. Цена донора приходила на экран сырой
 * строкой сервера — «250.00 / 180.00 EUR», с точкой и без разрядов, — рядом
 * с расходом, написанным «0,72 $»: два формата денег на соседних экранах
 * читаются как два приложения. Копейки — всегда: цена бывает и 249,99.
 * Валюта без знака пишется кодом; не названа — только число.
 */
export function formatMoney(
  value: number | string | null | undefined,
  currency: string | null | undefined,
): string {
  if (missing(value)) return '—';
  const code = (currency ?? '').trim();
  const sign = CURRENCY_SIGNS[code.toUpperCase()] ?? code;
  const amount = money.format(Number(value));
  return sign === '' ? amount : `${amount} ${sign}`;
}

/** Доллары: «0,72 $». Сервер отдаёт деньги строкой, чтобы не терять копейки. */
export function formatUsd(value: number | string | null | undefined): string {
  return formatMoney(value, 'USD');
}

/** Доля 0…1 процентами: «18%». */
export function formatShare(share: number | null | undefined): string {
  return share === null || share === undefined ? '—' : `${Math.round(share * 100)}%`;
}

/**
 * Доля 0…1 процентами с десятой: «25,4%», «19%» (нулевой хвост не пишется).
 *
 * Для чисел, которые сравнивают с границей: отличие письма 0,254 при коридоре
 * до 25% целыми процентами — «25% выше коридора 15–25%», фраза, которая
 * опровергает сама себя. `digits` — точнее десятой, когда и десятой мало.
 */
export function formatPercent(share: number | null | undefined, digits = 1): string {
  if (share === null || share === undefined || Number.isNaN(share)) return '—';
  const exact = Math.max(0, Math.min(Math.trunc(digits), 4));
  return `${percentFormat(exact).format(share * 100)}%`;
}

/** Слово при числе: `plural(4, 'письмо', 'письма', 'писем')` — «письма». */
export function plural(count: number, one: string, few: string, many: string): string {
  const whole = Math.abs(Math.trunc(count));
  const tens = whole % 100;
  const ones = whole % 10;
  if (tens >= 11 && tens <= 14) return many;
  if (ones === 1) return one;
  if (ones >= 2 && ones <= 4) return few;
  return many;
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
