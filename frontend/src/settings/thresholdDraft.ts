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
 *
 * **Поле — как набрано, строкой.** Числовое поле Mantine молча выбрасывало
 * точку, запятую, минус и буквы и склеивало оставшиеся цифры: «2.5» и «2,5»
 * становились 25, «1e3» — 13, «-5» — 5 (проверка QA 10.10.2026). Теперь набранное
 * стоит в поле, а под ним — почему такой порог нельзя (`components/numberText`).
 */

import type { ThresholdRange, ThresholdsBody } from '../api/types';
import { numberRefusal, numberText, validNumber } from '../components/numberText';
import type { NumberRule } from '../components/numberText';

export type ThresholdKey = keyof ThresholdsBody;

/** Поле как набрано. */
export type ThresholdDraft = Record<ThresholdKey, string>;

export type ThresholdLimits = Record<ThresholdKey, ThresholdRange>;

export const THRESHOLD_KEYS: ThresholdKey[] = [
  'min_dr',
  'min_org_traffic',
  'min_refdomains',
  'min_keywords',
];

/** Пороги в полях — числами по-русски, с разрядами: от них правят. */
export function draftOf(body: ThresholdsBody): ThresholdDraft {
  const draft: Partial<ThresholdDraft> = {};
  for (const key of THRESHOLD_KEYS) draft[key] = numberText(body[key]);
  return draft as ThresholdDraft;
}

/** Порог — целое число в границах, как у схемы сервера. */
function ruleOf(range: ThresholdRange): NumberRule {
  return { decimals: 0, min: range.min, max: range.max };
}

/** Почему значение сохранить нельзя — словами, или `null`, если можно. */
export function fieldRefusal(text: string, range: ThresholdRange): string | null {
  if (text.trim() === '') return 'Впишите число';
  return numberRefusal(text, ruleOf(range));
}

/** Черновик, готовый уйти на сервер, или `null`, если хоть одно поле
 *  не годится: предпросмотр и сохранение ждут все четыре. */
export function bodyOf(draft: ThresholdDraft, limits: ThresholdLimits): ThresholdsBody | null {
  const body: Partial<ThresholdsBody> = {};
  for (const key of THRESHOLD_KEYS) {
    const value = validNumber(draft[key], ruleOf(limits[key]));
    if (value === null) return null;
    body[key] = value;
  }
  return body as ThresholdsBody;
}
