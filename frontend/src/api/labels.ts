/**
 * Русские подписи к тому, что сервер называет по-английски.
 *
 * Сервер отдаёт именованные действия (`view`, `run`, …) — так они
 * и лежат в базе, в журнале и в отказах, и переименовывать их нельзя:
 * по этим именам разбирают инциденты. А человеку в интерфейсе нужны
 * слова его языка, поэтому перевод живёт здесь, одним местом на весь
 * фронт: разъехавшиеся подписи одного и того же действия на двух
 * экранах читаются как два разных права.
 */

import type {
  ContactSource,
  ContactStatus,
  DonorStatus,
  MessageStatus,
  Permission,
  ReplyKind,
  Role,
  RunStatus,
  ThreadState,
  UsageProvider,
} from './types';

export const PERMISSION_TITLES: Record<Permission, string> = {
  view: 'смотреть базу',
  run: 'запускать прогоны',
  settings: 'править пороги',
  send: 'отправлять письма',
  senders: 'домены рассылки',
  users: 'заводить учётки',
};

export const ROLE_TITLES: Record<Role, string> = {
  admin: 'админ',
  operator: 'оператор',
};

export function permissionTitle(permission: string): string {
  return PERMISSION_TITLES[permission as Permission] ?? permission;
}

/** Состояния диалога словами и цветом.
 *
 *  Цвет идёт от смысла: мята — дошло до цели, янтарь — нужно внимание,
 *  роза — тупик. Состояний семь, цветов четыре — и это намеренно:
 *  пятый цвет означал бы, что смысл потерян.
 */
export const THREAD_STATES: Record<ThreadState, { title: string; color: string }> = {
  queued: { title: 'в очереди', color: 'gray' },
  waiting: { title: 'ждём ответа', color: 'blue' },
  replied: { title: 'ответил человек', color: 'teal' },
  priced: { title: 'цена получена', color: 'green' },
  bounced: { title: 'отказ доставки', color: 'yellow' },
  unsubscribed: { title: 'отписался', color: 'red' },
  stopped: { title: 'цепочка остановлена', color: 'gray' },
};

export const MESSAGE_STATUSES: Record<MessageStatus, string> = {
  queued: 'в очереди',
  sending: 'отправляется',
  sent: 'принято платформой',
  delivered: 'доставлено',
  bounced: 'отказ доставки',
  stopped: 'остановлено',
};

export const REPLY_KINDS: Record<ReplyKind, { title: string; color: string }> = {
  human: { title: 'ответ человека', color: 'teal' },
  auto_reply: { title: 'автоответчик', color: 'gray' },
  bounce: { title: 'отказ доставки', color: 'yellow' },
  unsubscribe: { title: 'отписка', color: 'red' },
};

/** Вердикт по донору.
 *
 *  «Не проверен» — не «не подходит»: Ahrefs не вернул данных, и это повод
 *  добрать позже, а не закрыть домен. Поэтому у них разные цвета,
 *  а не один серый на двоих.
 */
export const DONOR_STATUSES: Record<DonorStatus, { title: string; color: string }> = {
  suitable: { title: 'подходит', color: 'green' },
  unsuitable: { title: 'не подходит', color: 'gray' },
  unchecked: { title: 'не проверен', color: 'yellow' },
};

export const CONTACT_STATUSES: Record<ContactStatus, { title: string; color: string }> = {
  found: { title: 'адрес найден', color: 'green' },
  not_found: { title: 'адреса нет', color: 'gray' },
  form_only: { title: 'только форма', color: 'yellow' },
  no_quota: { title: 'кончилась квота', color: 'yellow' },
  rate_limited: { title: 'предел запросов', color: 'yellow' },
  error: { title: 'ошибка поиска', color: 'red' },
};

/** Ступень лестницы, которая дала адрес. Порядок в подписях тот же,
 *  что и в самой лестнице: от бесплатных к платной. */
export const CONTACT_SOURCES: Record<ContactSource, string> = {
  mx: 'запись MX',
  page: 'страница сайта',
  rdap: 'RDAP',
  paid: 'платный сервис',
  form: 'форма на сайте',
  manual: 'заведён вручную',
};

export const RUN_STATUSES: Record<RunStatus, { title: string; color: string }> = {
  estimating: { title: 'считает смету', color: 'blue' },
  running: { title: 'идёт', color: 'blue' },
  done: { title: 'закончен', color: 'green' },
  stopped: { title: 'остановлен', color: 'yellow' },
};

/** Названия рынков. Список стран приходит с сервера — здесь только
 *  подписи к кодам; незнакомый код показывается как есть, а не пропадает. */
const COUNTRY_TITLES: Record<string, string> = {
  us: 'США',
  gb: 'Великобритания',
  de: 'Германия',
  fr: 'Франция',
  es: 'Испания',
  it: 'Италия',
  nl: 'Нидерланды',
  pl: 'Польша',
  ca: 'Канада',
  au: 'Австралия',
  in: 'Индия',
  br: 'Бразилия',
  mx: 'Мексика',
  id: 'Индонезия',
  ph: 'Филиппины',
  za: 'ЮАР',
  ae: 'ОАЭ',
  sa: 'Саудовская Аравия',
  tr: 'Турция',
  ua: 'Украина',
  kz: 'Казахстан',
  sg: 'Сингапур',
  my: 'Малайзия',
  th: 'Таиланд',
  vn: 'Вьетнам',
  jp: 'Япония',
  se: 'Швеция',
  no: 'Норвегия',
  dk: 'Дания',
  fi: 'Финляндия',
  cz: 'Чехия',
  ro: 'Румыния',
  gr: 'Греция',
  pt: 'Португалия',
  ie: 'Ирландия',
  nz: 'Новая Зеландия',
  il: 'Израиль',
  eg: 'Египет',
  ng: 'Нигерия',
  ke: 'Кения',
  ar: 'Аргентина',
  cl: 'Чили',
  co: 'Колумбия',
  pe: 'Перу',
  ch: 'Швейцария',
  at: 'Австрия',
  be: 'Бельгия',
  hu: 'Венгрия',
  bg: 'Болгария',
  hr: 'Хорватия',
  sk: 'Словакия',
  si: 'Словения',
  lt: 'Литва',
  lv: 'Латвия',
  ee: 'Эстония',
};

export function countryTitle(code: string): string {
  const title = COUNTRY_TITLES[code];
  return title === undefined ? code : `${title} · ${code}`;
}

/** На что уходят деньги. Подписи те же, что в отчёте прогона: расход
 *  и отчёт должны называть одно и то же одинаково. */
export const USAGE_PROVIDERS: Record<UsageProvider, { title: string; color: string }> = {
  ahrefs: { title: 'метрики Ahrefs', color: 'lagoon' },
  serp: { title: 'выдача', color: 'blue' },
  llm: { title: 'модель', color: 'grape' },
  email: { title: 'отправка', color: 'teal' },
};

/** Операции внутри провайдера. Незнакомая показывается как есть —
 *  новая операция не должна пропадать с экрана расхода. */
const OPERATION_TITLES: Record<string, string> = {
  batch_metrics: 'метрики пачкой',
  by_country: 'страны',
  dr_screen: 'просев по DR',
  serp_task: 'запрос выдачи',
  keywords: 'генерация ключей',
  judge: 'судья релевантности',
};

export function operationTitle(operation: string): string {
  return OPERATION_TITLES[operation] ?? operation;
}
