/**
 * Русские подписи к кодам раздела «Продажи»: состояния лида, причины отказа,
 * поля файла, шаги и языки цепочки писем. Подпись самого права `sales` — в `labels.ts`, рядом с остальными.
 *
 * Своим файлом по той же причине, что `salesTypes.ts`: `labels.ts` стоит
 * в тающем baseline длины, и новый раздел его не растит. Правило одно место —
 * одно слово держится: у каждого кода продаж подпись ровно здесь.
 */

import type {
  ChainLanguage,
  ChainPlaceholder,
  KbKind,
  LeadField,
  LeadState,
  SenderField,
  ZoneKind,
} from './salesTypes';

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
 *  код виден общими словами с кодом в скобках, а не пропадает. Что именно
 *  нашлось, говорят слова очистки рядом: «дубль» — и адрес, и компания в работе
 *  другой гипотезы; «не подтверждён» — второй `unknown` и домен, с которым
 *  проверка не работает. */
export const LEAD_REJECTION_REASONS: Record<string, string> = {
  duplicate: 'дубль',
  stoplist: 'стоп-лист продаж',
  unsubscribed: 'отписка',
  other_direction: 'домен в работе у другого направления',
  unusable: 'негодный адрес',
  no_mail: 'домен не принимает почту',
  undeliverable: 'адрес не существует',
  unconfirmed: 'адрес не подтверждён',
  undone: 'решение по сайту отменено',
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

/** Что знать о первом письме: зона rewrite — «может быть переписана», в отдельном письме
 *  сборка оставляет её текстом шаблона, если так письмо ложится в коридор отличия. */
const FIRST_STEP_HINT =
  'с темой; зоны rewrite модель может переписать под адресата — в отдельном письме зона ' +
  'может остаться текстом шаблона: сборка подбирает зоны под коридор отличия';

/** Что знать о добивке: зоны rewrite модель переписывает при сборке первого письма — у каждого
 *  лида по-своему, — и отправка по расписанию берёт текст готовым. */
const FOLLOWUP_HINT =
  'зоны rewrite модель переписывает при сборке первого письма, у каждого лида по-своему; ' +
  'зона может остаться текстом шаблона';

/** Шаг цепочки писем словами и что о нём знать. Номера — шаги сервера
 *  (`chain_text.STEPS`; сверку держит `tests/test_sales_chain_screen.py`). */
export const CHAIN_STEPS: Record<number, { title: string; hint: string }> = {
  1: { title: 'Первое письмо', hint: FIRST_STEP_HINT },
  2: { title: 'Первая добивка', hint: `в той же переписке, без темы; ${FOLLOWUP_HINT}` },
  3: { title: 'Вторая добивка', hint: `последнее письмо цепочки; ${FOLLOWUP_HINT}` },
};

/** Добивка в строке письма сборки — порядковым словом: «первая — отличие 19%». */
export const FOLLOWUP_ORDINALS: Record<number, string> = { 2: 'первая', 3: 'вторая' };

export function chainStepTitle(step: number): string {
  return CHAIN_STEPS[step]?.title ?? `шаг ${step}`;
}

/** Язык письма словами. Незнакомый код виден как есть. */
export const CHAIN_LANGUAGES: Record<ChainLanguage, string> = {
  ru: 'Русский',
  en: 'Английский',
};

export function chainLanguageTitle(code: string): string {
  return code in CHAIN_LANGUAGES ? CHAIN_LANGUAGES[code as ChainLanguage] : code;
}

/** Что сборка сделает с зоной — подпись у зоны в предпросмотре. */
export const ZONE_KINDS: Record<ZoneKind, string> = {
  rewrite: 'может переписать модель',
  fixed: 'уходит как есть',
};

/** Подстановки шаблона цепочки и что в них встанет. */
export const CHAIN_PLACEHOLDERS: Record<ChainPlaceholder, string> = {
  name: 'имя адресата',
  company: 'компания',
  site: 'сайт компании',
};

export function chainPlaceholderTitle(name: string): string {
  return name in CHAIN_PLACEHOLDERS ? CHAIN_PLACEHOLDERS[name as ChainPlaceholder] : name;
}
