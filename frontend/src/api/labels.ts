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
  HumanIntent,
  JudgeDecider,
  MessageStatus,
  Permission,
  ReplyKind,
  ReviewDecision,
  ReviewTier,
  Role,
  RunStatus,
  SelectionTab,
  SuppressionReason,
  ThreadState,
  UsageProvider,
} from './types';

export const PERMISSION_TITLES: Record<Permission, string> = {
  view: 'смотреть базу',
  run: 'запускать прогоны',
  settings: 'править пороги',
  prices: 'подтверждать цены',
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
  waiting: { title: 'ждём ответа', color: 'lagoon' },
  replied: { title: 'ответил человек', color: 'green' },
  needs_review: { title: 'ждёт разбора', color: 'yellow' },
  priced: { title: 'цена получена', color: 'green' },
  declined: { title: 'не продаёт размещения', color: 'gray' },
  free: { title: 'гостевой — бесплатно', color: 'green' },
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
  human: { title: 'ответ человека', color: 'green' },
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
  // Красный, а не жёлтый: жёлтое оператор ждёт, а здесь ждать нечего —
  // пока учётку не откроют, платная ступень мертва по всем доменам.
  blocked: { title: 'учётка закрыта', color: 'red' },
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
  queued: { title: 'в очереди', color: 'gray' },
  estimating: { title: 'считает смету', color: 'lagoon' },
  running: { title: 'идёт', color: 'lagoon' },
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

/** Страна одним видом на всех экранах: русское имя, а незнакомый код —
 *  заглавными («NG»). Раньше одна и та же страна была «США · us», «US»
 *  и «us» на трёх соседних экранах. */
export function countryTitle(code: string | null | undefined): string {
  if (code === null || code === undefined || code === '') return '—';
  return COUNTRY_TITLES[code.toLowerCase()] ?? code.toUpperCase();
}

/** На что уходят деньги. Подписи те же, что в отчёте прогона: расход
 *  и отчёт должны называть одно и то же одинаково. */
export const USAGE_PROVIDERS: Record<UsageProvider, { title: string; color: string }> = {
  ahrefs: { title: 'метрики Ahrefs', color: 'lagoon' },
  serp: { title: 'выдача', color: 'lagoon' },
  llm: { title: 'модель', color: 'gray' },
  email: { title: 'отправка', color: 'green' },
};

/** Операции внутри провайдера — все, что сервер пишет в журнал расхода
 *  (`OPERATION_PROVIDERS` в `backend/features/core/usage.py`). Незнакомая
 *  не пропадает с экрана: она показывается общими словами с кодом в
 *  скобках, чтобы новую операцию было видно и было по чему её найти. */
const OPERATION_TITLES: Record<string, string> = {
  batch_metrics: 'метрики доменов',
  by_country: 'трафик по странам',
  serp: 'обзор выдачи Ahrefs',
  serp_search: 'поиск по выдаче',
  keywords: 'сборка ключей',
  site_judge: 'судья площадок',
  letter_rewrite: 'переписывание писем',
  letter_send: 'отправка писем',
  reply_parse: 'разбор ответов',
  // Прежние имена: в журнале они остались у старых строк.
  dr_screen: 'просев по DR',
  serp_task: 'запрос выдачи',
  judge: 'судья площадок',
};

export function operationTitle(operation: string): string {
  return OPERATION_TITLES[operation] ?? `прочее (${operation})`;
}

/** Настройки почты, без которых письмо не уходит. Сервер называет их
 *  именами в окружении — так их ищет тот, кто подключает почту; человеку
 *  на экране нужно, что именно не задано, а не как это называется
 *  в конфигурации. Незнакомая — общими словами, а не кодом. */
const MAIL_SETTING_TITLES: Record<string, string> = {
  OUTREACH_SENDER_NAME: 'имя отправителя',
  OUTREACH_POSTAL_ADDRESS: 'почтовый адрес',
  OUTREACH_UNSUBSCRIBE_URL: 'адрес страницы отписки',
  OUTREACH_INBOUND_SECRET: 'ключ ссылок отписки',
};

export function mailSettingTitle(key: string): string {
  return MAIL_SETTING_TITLES[key] ?? 'настройка почты';
}

/** Перечень незаданных настроек почты словами, без повторов. */
export function mailSettingsList(keys: string[]): string {
  return [...new Set(keys.map(mailSettingTitle))].join(', ');
}

/** Почему адресату не пишем. Первые две — его решение, вторые две — наше. */
export const SUPPRESSION_REASON_TITLES: Record<SuppressionReason, string> = {
  unsubscribed: 'отписался',
  complained: 'пожаловался',
  supplier: 'поставщик',
  manual: 'вручную',
};

/** Вкладки отбора. Порядок — порядок работы: сначала то, что принято. */
export const SELECTION_TABS: Record<SelectionTab, { title: string; color: string }> = {
  accepted: { title: 'Приняты', color: 'green' },
  review: { title: 'К разбору', color: 'yellow' },
  rejected: { title: 'Отклонены', color: 'red' },
};

/** Кто вынес вердикт судьи — словами оператора, а не кодами. */
export const JUDGE_DECIDERS: Record<JudgeDecider, { title: string; hint: string }> = {
  rule: { title: 'правило', hint: 'выдача и главная сказали одно' },
  model: { title: 'модель', hint: 'по тексту выдачи' },
  arbiter: { title: 'арбитр', hint: 'выдача и главная спорили' },
};

export const JUDGE_ADVICE: Record<
  'accept' | 'review' | 'reject',
  { title: string; color: string }
> = {
  accept: { title: 'площадка', color: 'green' },
  review: { title: 'посмотри', color: 'yellow' },
  reject: { title: 'не площадка', color: 'red' },
};

/** Что человек говорит о сайте. */
export const HUMAN_INTENTS: Record<HumanIntent, string> = {
  publisher: 'Площадка',
  sells_own: 'Продаёт своё',
  non_commercial: 'Не продаёт места',
};

/** Тип сайта по судье — словами оператора. Ярлык у каждого кандидата:
 *  по нему видно, какой тип судья путает чаще всего. */
export const SITE_INTENTS: Record<string, string> = {
  sells_placement: 'продаёт размещение',
  link_vendor: 'посредник',
  editorial_ads: 'издание',
  refers_out: 'обзорщик',
  sells_own: 'продаёт своё',
  non_commercial: 'некоммерческий',
  none: 'не понять',
  unknown: 'не понять',
};

export const REVIEW_DECISIONS: Record<ReviewDecision, { title: string; color: string }> = {
  pending: { title: 'Предложены', color: 'yellow' },
  accepted: { title: 'Приняты', color: 'green' },
  rejected: { title: 'Отклонены', color: 'red' },
};

/** Ярус очереди — почему кандидат стоит там, где стоит. */
export const REVIEW_TIERS: Record<ReviewTier, { title: string; color: string }> = {
  likely: { title: 'вероятно донор', color: 'green' },
  open: { title: 'посмотреть', color: 'gray' },
  doubtful: { title: 'сомнительно', color: 'red' },
};
