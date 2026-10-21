/**
 * Журнал действий словами: действие, цель и подробности записи.
 *
 * Сервер пишет журнал кодами (`login_failed`, `run:27`, `{"role": "admin"}`): по ним
 * разбирают инциденты, и переименовывать их нельзя. Человеку на экране нужны слова —
 * по образцу `labels.ts`, одним местом на весь фронт. Отдельным файлом: `labels.ts`
 * у потолка длины.
 *
 * **Незнакомое не пропадает и не роняет экран.** Сервер новее экрана — действие, цель
 * или значение, которых здесь нет, показываются общими словами с кодом, а не пустотой:
 * так новое видно, и есть по чему его найти.
 */

import type { AuditAction } from './journal';
import {
  CONTACT_STATUSES,
  HUMAN_INTENTS,
  JUDGE_ADVICE,
  permissionTitle,
  ROLE_TITLES,
  SUPPRESSION_REASON_TITLES,
} from './labels';
import { formatNumber } from '../format';

/** Действие словами. Сверку с сервером держит `tests/test_journal_words.py`. */
export const AUDIT_ACTIONS: Record<AuditAction, string> = {
  login: 'Вход',
  login_failed: 'Вход не удался',
  password_changed: 'Пароль сменён',
  user_created: 'Учётка заведена',
  // Общее «изменено»: доступ учётки, правка письма, ящик рассылки, рассмотрение
  // прогона. Что именно — в подробностях, ключом «действие».
  user_updated: 'Правка',
  // Прогон, сборка писем, обход донора, задачи продаж — что именно, в подробностях.
  run_started: 'Запуск',
  thresholds_changed: 'Пороги изменены',
  letter_sent: 'Письмо отправлено',
  price_reviewed: 'Цена разобрана',
  contact_added: 'Адрес внесён',
  contact_removed: 'Адрес удалён',
  advertiser_reviewed: 'Решение по рекламодателю',
  site_reviewed: 'Тип сайта назван',
  suppression_added: 'Добавлено в стоп-лист',
  suppression_removed: 'Снято со стоп-листа',
  lead_taken: 'Лид взят в работу',
  sales_leads_imported: 'База лидов загружена',
  agent_settings_changed: 'Настройки агента изменены',
  sales_kb_changed: 'База знаний продаж изменена',
  data_pruned: 'Чистка данных',
  probe_created: 'Проверочная запись заведена',
  sales_chain_changed: 'Цепочка писем продаж изменена',
  agent_draft_decided: 'Решение по черновику агента',
  // Кап юнитов Ahrefs сменён на экране «Расход» — в подробностях «было → стало» и лимит ключа.
  ahrefs_cap_changed: 'Кап Ahrefs изменён',
};

/** Действия этапа продаж: без права «Продажи» их нет ни в журнале, ни в фильтре (П2). */
export const SALES_ACTIONS: ReadonlySet<string> = new Set<AuditAction>([
  'sales_leads_imported',
  'sales_kb_changed',
  'sales_chain_changed',
]);

/** Подпись действия в фильтре: у общих «Правки» и «Запуска» — что под ними бывает. */
const FILTER_TITLES: Partial<Record<AuditAction, string>> = {
  user_updated: 'Правка — доступ, письмо, ящик, рассмотрение',
  run_started: 'Запуск — прогон, сборка писем, обход',
};

export function actionFilterTitle(action: AuditAction): string {
  return FILTER_TITLES[action] ?? AUDIT_ACTIONS[action];
}

/** Общее действие, уточнённое видом цели: «действие вид» → слова. Правка лида продаж
 *  пишется действием загрузки базы — у лида одна история данных (`sales/lead_edit.py`). */
const BY_TARGET: Partial<Record<string, string>> = {
  'user_updated user': 'Доступ учётки изменён',
  'run_started run': 'Прогон запущен',
  'run_started crawl': 'Обход донора запущен',
  'sales_leads_imported sales_lead': 'Лид продаж поправлен',
  'site_reviewed sales_segment_candidate': 'Решение по сайту сбора',
  'lead_taken sales_handoff': 'Лид передан телемаркетологу',
};

/** Действие записи словами — с уточнением по цели там, где действие общее. */
export function actionTitle(action: string, target: string | null): string {
  const kind = target?.split(':', 1)[0];
  const known: Partial<Record<string, string>> = AUDIT_ACTIONS;
  return BY_TARGET[`${action} ${kind}`] ?? known[action] ?? `Другое действие (${action})`;
}

/** Цель записи — по виду: «run» → «прогон №27». Ссылка — у тех, у кого есть экран.
 *  `bare` — номер строки человеку ничего не говорит: у порогов и настроек агента
 *  версия — в подробностях, у задачи очереди номер — служебный. */
const TARGETS: Record<string, { noun: string; path?: (id: string) => string; bare?: true }> = {
  run: { noun: 'прогон', path: (id) => `/runs/${id}` },
  donor: { noun: 'донор', path: (id) => `/donors/${id}` },
  user: { noun: 'учётка' },
  message: { noun: 'письмо' },
  reply: { noun: 'ответ' },
  job: { noun: 'задача в очереди', bare: true },
  crawl: { noun: 'обход' },
  candidate: { noun: 'кандидат в рекламодатели' },
  advertiser: { noun: 'рекламодатель' },
  domain: { noun: 'домен' },
  suppression: { noun: 'запись стоп-листа' },
  sender: { noun: 'ящик рассылки' },
  settings: { noun: 'пороги', bare: true },
  agent_settings: { noun: 'настройки агента', bare: true },
  // Номер строки смены человеку ничего не говорит: что стало с капом — в подробностях.
  ahrefs_cap: { noun: 'кап Ahrefs', bare: true },
  agent_draft: { noun: 'черновик агента' },
  sales_hypothesis: { noun: 'гипотеза продаж' },
  sales_handoff: { noun: 'передача лида продаж' },
  sales_chain_template: { noun: 'шаблон цепочки продаж' },
  sales_kb_entry: { noun: 'запись базы знаний продаж' },
  sales_lead: { noun: 'лид продаж' },
  // Без номера — весь стоп-лист продаж (`WHOLE_TARGETS`), с номером — снятая запись.
  sales_stoplist: { noun: 'запись стоп-листа продаж' },
  sales_segment_candidate: { noun: 'сайт сбора продаж' },
};

/** Цели без номера — целиком. */
const WHOLE_TARGETS: Record<string, string> = {
  sales_kb: 'база знаний продаж',
  sales_chain: 'цепочка писем продаж',
  sales_settings: 'отправитель продаж',
  sales_stoplist: 'стоп-лист продаж',
  'advertisers:promote': 'перевод в рекламодатели',
};

export interface TargetPart {
  text: string;
  /** Адрес экрана цели. Нет — у цели нет своего экрана. */
  to?: string;
}

type KnownTarget = (typeof TARGETS)[string];

/** «run:27» → вид и номер; без двоеточия — вид без номера. */
function split(part: string): [string, string] {
  const at = part.indexOf(':');
  return at < 0 ? [part, ''] : [part.slice(0, at), part.slice(at + 1)];
}

/** Цель знакомого вида: с номером — «прогон №27» и ссылка, если у вида есть экран. */
function knownPart(
  kind: string,
  id: string,
  known: KnownTarget,
  emails: ReadonlyMap<number, string>,
): TargetPart {
  if (known.bare === true) return { text: known.noun };
  if (!/^\d+$/.test(id)) return { text: `${known.noun} ${id}` };
  const email = kind === 'user' ? emails.get(Number(id)) : undefined;
  if (email !== undefined) return { text: `учётка ${email}` };
  const text = `${known.noun} №${id}`;
  return known.path === undefined ? { text } : { text, to: known.path(id) };
}

/** Одна часть цели словами. `emails` — почта учётки по номеру: «учётка ivan@…». */
function targetPart(part: string, emails: ReadonlyMap<number, string>): TargetPart {
  const whole = WHOLE_TARGETS[part];
  if (whole !== undefined) return { text: whole };
  const [kind, id] = split(part);
  const known = TARGETS[kind];
  if (known === undefined || id === '') return { text: part };
  return knownPart(kind, id, known, emails);
}

/** Цель записи словами. У чистки цель — перечень через запятую: каждая часть — своими. */
export function targetParts(
  target: string | null,
  emails: ReadonlyMap<number, string>,
): TargetPart[] {
  if (target === null || target === '') return [];
  return target.split(', ').map((part) => targetPart(part.trim(), emails));
}

/** Ключи подробностей, которые сервер пишет по-английски. Остальные — уже словами. */
const KEY_WORDS: Record<string, string> = {
  email: 'почта',
  role: 'роль',
  reason: 'причина',
  is_active: 'учётка',
  permissions: 'права',
  dr: 'DR',
};

const lower = (word: string) => word.charAt(0).toLowerCase() + word.slice(1);

const INTENT_WORDS: Record<string, string> = Object.fromEntries(
  Object.entries(HUMAN_INTENTS).map(([code, title]) => [code, lower(title)]),
);

const SUPPRESSION_WORDS: Record<string, string> = SUPPRESSION_REASON_TITLES;

/** Решение человека кодом записи — у решения и у его отмены («Отменить решение», сбор базы продаж). */
const DECISION_WORDS: Record<string, string> = {
  accepted: 'принят',
  rejected: 'отклонён',
  pending: 'снова ждёт решения',
  ...INTENT_WORDS,
};

/** Значения-коды под ключом — словами экрана. Незнакомое значение — как записано. */
const VALUE_WORDS: Record<string, Record<string, string>> = {
  решение: DECISION_WORDS,
  отменено: DECISION_WORDS,
  было: INTENT_WORDS,
  судья: Object.fromEntries(
    Object.entries(JUDGE_ADVICE).map(([code, advice]) => [code, advice.title]),
  ),
  этап: { donors: 'доноры', advertisers: 'рекламодатели', sales: 'продажи' },
  причина: SUPPRESSION_WORDS,
  'была причина': SUPPRESSION_WORDS,
  role: ROLE_TITLES,
  'исход поиска после': Object.fromEntries(
    Object.entries(CONTACT_STATUSES).map(([code, status]) => [code, status.title]),
  ),
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Точечные права учётки — словами окна прав: «отправлять письма — выдано». */
function rightsWords(rights: Record<string, unknown>): string {
  const listed = Object.entries(rights);
  if (listed.length === 0) return 'как у роли';
  return listed
    .map(([right, on]) => `${permissionTitle(right)} — ${on === true ? 'выдано' : 'снято'}`)
    .join(', ');
}

/** Ключи, значение которых читается по-своему. `undefined` — читать общим правилом. */
const OWN_WORDS: Record<string, (value: unknown) => string | undefined> = {
  permissions: (value) => (isRecord(value) ? rightsWords(value) : undefined),
  is_active: (value) =>
    typeof value === 'boolean' ? (value ? 'включена' : 'отключена') : undefined,
};

/** Да/нет, число с разрядами, код — словом под своим ключом, остальное — как записано. */
function scalarWords(key: string, value: string | number | boolean): string {
  if (typeof value === 'boolean') return value ? 'да' : 'нет';
  if (typeof value === 'number') {
    return Number.isInteger(value) ? formatNumber(value) : value.toLocaleString('ru-RU');
  }
  return VALUE_WORDS[key]?.[value] ?? value;
}

/** Список — через запятую, «было/стало» — стрелкой, прочее — «ключ: значение; …». */
function objectWords(key: string, value: object): string {
  if (Array.isArray(value)) {
    return value.length === 0 ? '—' : value.map((item) => valueWords(key, item)).join(', ');
  }
  const record = value as Record<string, unknown>;
  if ('было' in record && 'стало' in record) {
    return `${valueWords(key, record['было'])} → ${valueWords(key, record['стало'])}`;
  }
  return Object.entries(record)
    .map(([inner, item]) => `${KEY_WORDS[inner] ?? inner}: ${valueWords(inner, item)}`)
    .join('; ');
}

/** Значение подробности словами. Из JSON приходят только строки, числа, да/нет, списки
 *  и словари — прочее (его не бывает) читается прочерком, а не «[object Object]». */
function valueWords(key: string, value: unknown): string {
  if (value === null || value === undefined) return '—';
  const own = OWN_WORDS[key]?.(value);
  if (own !== undefined) return own;
  if (typeof value === 'object') return objectWords(key, value);
  const scalar =
    typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean';
  return scalar ? scalarWords(key, value) : '—';
}

export interface DetailLine {
  label: string;
  value: string;
}

/** Подробности записи строками «ключ: значение». Пустые — не строкой «—»: их не было.
 *  `skip` — ключи, которые экран уже показал в другом месте («действие» — под названием). */
export function detailLines(
  details: Record<string, unknown> | null,
  skip: ReadonlySet<string> = new Set(),
): DetailLine[] {
  if (details === null) return [];
  return Object.entries(details)
    .filter(([key, value]) => !skip.has(key) && value !== null && value !== undefined)
    .filter(([, value]) => value !== '' && !(Array.isArray(value) && value.length === 0))
    .map(([key, value]) => ({ label: KEY_WORDS[key] ?? key, value: valueWords(key, value) }));
}
