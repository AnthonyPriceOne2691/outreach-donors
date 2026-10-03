/**
 * Русские подписи к кодам раздела «Продажи»: состояния лида, причины отказа,
 * поля файла. Подпись самого права `sales` — в `labels.ts`, рядом с остальными.
 *
 * Своим файлом по той же причине, что `salesTypes.ts`: `labels.ts` стоит
 * в тающем baseline длины, и новый раздел его не растит. Правило одно место —
 * одно слово держится: у каждого кода продаж подпись ровно здесь.
 */

import type { LeadField, LeadState } from './salesTypes';

/** Состояние лида продаж словами и цветом. «Новый» — загружен и ещё не
 *  очищен: серый, ждать очистки; «готов» — прошёл очистку, можно в письма;
 *  «отклонён» — отсеян, причина рядом. */
export const LEAD_STATES: Record<LeadState, { title: string; color: string }> = {
  new: { title: 'новый', color: 'gray' },
  ready: { title: 'готов', color: 'green' },
  rejected: { title: 'отклонён', color: 'red' },
};

/** Причина отказа лида словами. Коды пишет очистка (`RejectionReason`
 *  сервера; сверку держит `tests/test_api_sales_screen.py`). Список причин для
 *  фильтра экран берёт из ответа сервера — здесь только слова, и незнакомый
 *  код виден общими словами с кодом в скобках, а не пропадает. */
export const LEAD_REJECTION_REASONS: Record<string, string> = {
  duplicate: 'дубль',
  stoplist: 'стоп-лист продаж',
  unsubscribed: 'отписка',
  other_direction: 'домен в работе у другого направления',
  unusable: 'негодный адрес',
  no_mail: 'домен не принимает почту',
  undeliverable: 'адрес не существует',
};

export function leadReasonTitle(code: string): string {
  return LEAD_REJECTION_REASONS[code] ?? `другая причина (${code})`;
}

/** Поля лида, в которые ложатся колонки файла, — подписи мастера загрузки.
 *  «Имя целиком» и «имя» с «фамилией» — разные поля: вторые сервер складывает. */
export const LEAD_FIELDS: Record<LeadField, string> = {
  email: 'почта',
  name: 'имя целиком',
  first_name: 'имя',
  last_name: 'фамилия',
  position: 'должность',
  company: 'компания',
  website: 'сайт компании',
  country: 'страна',
  timezone: 'часовой пояс',
  language: 'язык письма',
};
