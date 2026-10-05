/**
 * Русские подписи к кодам раздела «Продажи»: состояния лида, причины отказа,
 * поля файла. Подпись самого права `sales` — в `labels.ts`, рядом с остальными.
 *
 * Своим файлом по той же причине, что `salesTypes.ts`: `labels.ts` стоит
 * в тающем baseline длины, и новый раздел его не растит. Правило одно место —
 * одно слово держится: у каждого кода продаж подпись ровно здесь.
 */

import type { KbKind, LeadField, LeadState, SenderField } from './salesTypes';

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

/** Вид записи базы знаний словами и что в такую запись пишут. Коды — `KbKind`
 *  сервера; незнакомый код виден общими словами с кодом в скобках. */
export const KB_KINDS: Record<KbKind, { title: string; hint: string }> = {
  brief: { title: 'о компании', hint: 'кто мы и что делаем; фон каждого письма' },
  service: { title: 'услуга', hint: 'что входит и кому' },
  case: { title: 'кейс', hint: 'что сделали и что вышло' },
  objection: { title: 'возражение', hint: 'возражение и ответ на него' },
  price_policy: { title: 'цены', hint: 'что можно говорить о цене и чего нельзя' },
  forbidden: { title: 'нельзя', hint: 'чего не писать никогда' },
  cta: { title: 'призыв', hint: 'чем закончить письмо: созвон, Telegram' },
};

export function kbKindTitle(kind: string): string {
  return kind in KB_KINDS ? KB_KINDS[kind as KbKind].title : `другой вид (${kind})`;
}

/** Поля отправителя: подпись, пояснение и в несколько ли строк. Порядок — парами
 *  формы: имя и должность, подпись и адрес (оба столбиком), сайт и Telegram. */
export const SENDER_FIELDS: Record<
  SenderField,
  { label: string; hint: string; multiline?: boolean }
> = {
  sender_name: { label: 'Имя отправителя', hint: 'настоящее имя: им подписано письмо' },
  sender_position: { label: 'Должность', hint: 'рядом с именем в подписи' },
  signature: {
    label: 'Подпись',
    hint: 'без неё письмо продаж не уходит',
    multiline: true,
  },
  physical_address: {
    label: 'Физический адрес',
    hint: 'обязателен по закону о рассылках: без него письмо продаж не уходит',
    multiline: true,
  },
  website: { label: 'Сайт', hint: 'ссылка https://…' },
  telegram: { label: 'Telegram для лидов', hint: '@имя или https://t.me/имя' },
  call_link: { label: 'Ссылка на созвон', hint: 'ссылка https://… на запись в календарь' },
};
