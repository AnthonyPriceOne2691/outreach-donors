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
  SelectionAnswer,
  SelectionHuman,
  SelectionJudge,
  SelectionTab,
  SelectionThresholds,
  SellerAnswerKind,
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
  lead: { title: 'лид — ждёт человека', color: 'yellow' },
  lead_taken: { title: 'лид в работе', color: 'green' },
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
 *  что и в самой лестнице: от бесплатных к платной. Адрес, вписанный по
 *  ответу на форму, — «вручную»: его нашла не лестница, а человек. */
export const CONTACT_SOURCES: Record<ContactSource, string> = {
  page: 'страница сайта',
  whois: 'данные регистратора',
  provider: 'платный сервис',
  manual: 'вписан вручную',
};

/** Адрес ещё не искали: исхода нет вовсе. Отдельным словом, а не пустым
 *  местом и не «адреса нет» — «не нашли» и «не искали» решаются по-разному:
 *  первое ждёт срока, второе — поиска. */
export const NOT_SEARCHED = { title: 'не искали', color: 'gray' } as const;

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

/** Слова ячеек отбора, общие со значениями фильтров под колонками
 *  (26.09.2026): фильтр называет значение тем же словом, что стоит
 *  в строке, — иначе, выбрав «до Ahrefs не дошёл», человек ищет глазами
 *  другое слово. */
export const NOT_REACHED = 'до Ahrefs не дошёл';
export const JUDGE_ABSENT = 'судья не смотрел';
export const NO_ANSWER = 'не отвечал';

/** Ответ донора, продаёт ли он размещение. */
export const SELLER_ANSWERS: Record<SellerAnswerKind, { title: string; color: string }> = {
  sells: { title: 'продаёт', color: 'green' },
  free: { title: 'берёт бесплатно', color: 'green' },
  declines: { title: 'не продаёт', color: 'red' },
};

/** Фильтр под «Порогами»: вердикт донора — или донора нет вовсе. */
export const SELECTION_THRESHOLDS: Record<SelectionThresholds, string> = {
  suitable: DONOR_STATUSES.suitable.title,
  unsuitable: DONOR_STATUSES.unsuitable.title,
  unchecked: DONOR_STATUSES.unchecked.title,
  none: NOT_REACHED,
};

/** Фильтр под «Судьёй» — кто вынес вердикт. Прежняя подпись «Кто решил
 *  у судьи» читалась загадкой (замечание 26.09.2026). Значения — слова
 *  значков в строке, а что за ними стоит, пункт списка говорит сам. */
export const SELECTION_JUDGES: Record<SelectionJudge, { title: string; hint?: string }> = {
  rule: JUDGE_DECIDERS.rule,
  model: JUDGE_DECIDERS.model,
  arbiter: JUDGE_DECIDERS.arbiter,
  none: { title: JUDGE_ABSENT },
};

/** Фильтр под «Донор ответил»: ответил ли — и что именно. */
export const SELECTION_ANSWERS: Record<SelectionAnswer, string> = {
  answered: 'ответил',
  none: NO_ANSWER,
  sells: SELLER_ANSWERS.sells.title,
  free: SELLER_ANSWERS.free.title,
  declines: SELLER_ANSWERS.declines.title,
};

/** Фильтр под «Человеком» — одно поле вместо двух переключателей
 *  («Только расхождения», «Человек не смотрел»), которые друг друга
 *  исключали. Те же слова стоят в строке. */
export const SELECTION_HUMAN: Record<SelectionHuman, string> = {
  unreviewed: 'не смотрел',
  reviewed: 'смотрел',
  disagrees: 'разошёлся с судьёй',
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

/** Наборы углов для сборки ключей моделью (`keywords/angles.py`). Список
 *  приходит с сервера кодами — здесь только подписи; новый набор, которого
 *  здесь ещё нет, виден общими словами с кодом, а не голым кодом. */
const KEYWORD_PRESET_TITLES: Record<string, string> = {
  wide: 'широкий охват',
  guest: 'гостевые посты',
  media: 'новости и издания',
  reviews: 'обзоры и подборки',
  guides: 'инструкции и правила',
};

export function presetTitle(code: string): string {
  return KEYWORD_PRESET_TITLES[code] ?? `другой набор (${code})`;
}

/** Языки рынка. Сервер называет их по-английски — так их понимает модель
 *  (`serp/markets.py`), а на экране они по-русски. */
const LANGUAGE_TITLES: Record<string, string> = {
  Arabic: 'арабский',
  Bulgarian: 'болгарский',
  Croatian: 'хорватский',
  Czech: 'чешский',
  Danish: 'датский',
  Dutch: 'нидерландский',
  English: 'английский',
  Estonian: 'эстонский',
  Filipino: 'филиппинский',
  Finnish: 'финский',
  French: 'французский',
  German: 'немецкий',
  Greek: 'греческий',
  Hebrew: 'иврит',
  Hungarian: 'венгерский',
  Indonesian: 'индонезийский',
  Italian: 'итальянский',
  Japanese: 'японский',
  Kazakh: 'казахский',
  Latvian: 'латышский',
  Lithuanian: 'литовский',
  Malay: 'малайский',
  Norwegian: 'норвежский',
  Polish: 'польский',
  Portuguese: 'португальский',
  Romanian: 'румынский',
  Russian: 'русский',
  Slovak: 'словацкий',
  Slovenian: 'словенский',
  Spanish: 'испанский',
  Swedish: 'шведский',
  Thai: 'тайский',
  Turkish: 'турецкий',
  Ukrainian: 'украинский',
  Vietnamese: 'вьетнамский',
};

export function languageTitle(name: string): string {
  return LANGUAGE_TITLES[name] ?? `другой язык (${name})`;
}

/** Признаки продажи своего на главной (`donors/home_signals.py`): сервер
 *  пишет их метками вида `cart:/warenkorb`, `path:/pricing`,
 *  `schema:SoftwareApplication`. Человеку нужно, что именно нашлось. */
const SCHEMA_TITLES: Record<string, string> = {
  Product: 'разметка товара с ценой',
  OfferCatalog: 'каталог товаров в разметке',
  LocalBusiness: 'разметка местной фирмы',
  ProfessionalService: 'разметка услуги',
  FinancialService: 'разметка финансовой услуги',
  InsuranceAgency: 'разметка страховой',
  Dentist: 'разметка клиники',
  MedicalBusiness: 'разметка клиники',
  MedicalClinic: 'разметка клиники',
  LegalService: 'разметка юридической услуги',
  SoftwareApplication: 'разметка программы',
  WebApplication: 'разметка программы',
};

const SERVICE_PATH_TITLES: Record<string, string> = {
  pricing: 'страница тарифов',
  demo: 'запись на демо',
  'request-a-demo': 'запись на демо',
  'book-a-demo': 'запись на демо',
  'contact-sales': 'связь с продажами',
  'free-trial': 'пробный период',
  'get-started': 'кнопка «начать»',
  appointment: 'запись на приём',
  appointments: 'запись на приём',
  'book-appointment': 'запись на приём',
  'get-a-quote': 'запрос цены',
  'request-a-quote': 'запрос цены',
};

function homeSignalTitle(mark: string): string {
  const cut = mark.indexOf(':');
  const kind = cut < 0 ? mark : mark.slice(0, cut);
  const value = cut < 0 ? '' : mark.slice(cut + 1);
  if (kind === 'cart') return value === 'слово' ? 'кнопка корзины' : 'ссылка на корзину';
  if (kind === 'engine') return 'движок магазина';
  if (kind === 'og' && value === 'product') return 'страница товара в разметке';
  if (kind === 'schema') {
    // `ElectronicsStore`, `HomeGoodsStore` и прочие — подтипы магазина.
    if (value.endsWith('Store')) return 'разметка магазина';
    return SCHEMA_TITLES[value] ?? `другой признак (${mark})`;
  }
  if (kind === 'path') {
    return SERVICE_PATH_TITLES[value.replace(/^\//, '')] ?? `другой признак (${mark})`;
  }
  return `другой признак (${mark})`;
}

/** Признаки главной словами, без повторов: «ссылка на корзину, разметка
 *  товара с ценой». Две метки одного смысла (`/demo` и `/book-a-demo`) —
 *  одно слово. */
export function homeSignalsText(marks: string[]): string {
  return [...new Set(marks.map(homeSignalTitle))].join(', ');
}

// --- письма и почта: имена настроек в тексте отказа (второй проход, 25.09.2026) ---

/** Настройки почты, которые сервер называет в тексте отказа транспорта:
 *  «OUTREACH_SENDGRID_API_KEY не задан — …». Текст пишется для журнала,
 *  и имя переменной там на месте; на экране — словами. */
const MAIL_SETTING_WORDS: Record<string, string> = {
  OUTREACH_SENDGRID_API_KEY: 'ключ почтовой платформы',
  OUTREACH_ALLOWED_RECIPIENTS: 'список разрешённых получателей',
  OUTREACH_REPLY_DOMAIN: 'домен для ответов',
  OUTREACH_TRANSPORT: 'способ отправки',
};

/** Текст сервера без имён переменных окружения: каждое — словами. Имя
 *  переменной на экране ничего не говорит тому, кто его читает, а ищут его
 *  по журналу сервера, где оно осталось. */
export function settingsInWords(text: string): string {
  const spoken = text.replace(
    /OUTREACH_[A-Z_]+/g,
    (key) => MAIL_SETTING_WORDS[key] ?? mailSettingTitle(key),
  );
  return spoken === text ? text : spoken.charAt(0).toUpperCase() + spoken.slice(1);
}
