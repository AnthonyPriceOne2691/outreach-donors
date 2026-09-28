/**
 * Черновик порогов и его проверка — теми же границами, по которым отказывает
 * сервер (замечание 28.09.2026: «валидация на допустимые значения»).
 *
 * **Границы приходят с сервера** (`ThresholdsView.limits`), своей копии чисел
 * у экрана нет: до этого экран знал только верх DR, трафик в сто миллионов
 * уходил на сервер, возвращался отказом по-английски, а блок «Что станет
 * с базой» тем временем показывал прежний ответ как ответ на новый порог.
 *
 * **Пустое поле — не ноль.** Раньше стёртое поле тихо становилось нулём, и
 * «сохранить» заводило версию без порога, которую никто не набирал.
 */

import type { ThresholdRange, ThresholdsBody } from '../api/types';
import { formatNumber } from '../format';

export type ThresholdKey = keyof ThresholdsBody;

/** Поле как набрано: число или пусто. */
export type ThresholdDraft = Record<ThresholdKey, number | ''>;

export type ThresholdLimits = Record<ThresholdKey, ThresholdRange>;

export const THRESHOLD_KEYS: ThresholdKey[] = [
  'min_dr',
  'min_org_traffic',
  'min_refdomains',
  'min_keywords',
];

/** Что набрано в поле. `NumberInput` отдаёт строку, когда поле пусто или
 *  число длиннее безопасного целого, — такое число честно остаётся числом
 *  и не проходит проверку границ, а не обрезается. */
export function typed(value: number | string): number | '' {
  if (typeof value === 'number') return value;
  const digits = value.replace(/\s/g, '');
  if (digits === '') return '';
  const number = Number(digits);
  return Number.isNaN(number) ? '' : number;
}

/** «от 0 до 10 000 000» — числами по-русски. */
export function rangeText(range: ThresholdRange): string {
  return `от ${formatNumber(range.min)} до ${formatNumber(range.max)}`;
}

/** Почему значение сохранить нельзя — словами, или `null`, если можно. */
export function fieldRefusal(value: number | '', range: ThresholdRange): string | null {
  if (value === '') return 'Впишите число';
  if (!Number.isInteger(value)) return 'Только целое число';
  if (value < range.min || value > range.max) return `Допустимо ${rangeText(range)}`;
  return null;
}

/** Черновик, готовый уйти на сервер, или `null`, если хоть одно поле
 *  не годится: предпросмотр и сохранение ждут все четыре. */
export function bodyOf(draft: ThresholdDraft, limits: ThresholdLimits): ThresholdsBody | null {
  const body: Partial<ThresholdsBody> = {};
  for (const key of THRESHOLD_KEYS) {
    const value = draft[key];
    if (value === '' || fieldRefusal(value, limits[key]) !== null) return null;
    body[key] = value;
  }
  return body as ThresholdsBody;
}
