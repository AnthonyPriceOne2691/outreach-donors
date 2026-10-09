/**
 * Вкладка, фильтры лидов, гипотеза раздела и период воронки — в адресе раздела «Продажи».
 *
 * **Адрес, а не состояние компонента** — как у отбора и доноров. Человек
 * обновляет вкладку браузера, возвращается «назад», присылает ссылку коллеге —
 * и видит те же фильтры и ту же страницу: `?state=rejected&reason=duplicate`
 * переживает перезагрузку, потому что перезагрузка — это чтение адреса заново.
 *
 * **Адрес — чужой ввод.** Его правят руками и присылают устаревшим, поэтому он
 * разбирается строго: незнакомое состояние, номер не числом, страница меньше
 * единицы — «не сужать», а не отказ сервера и не пустая таблица без объяснения.
 * Код причины сверяется только по форме (так пишет очистка): список причин
 * знает сервер и отдаёт его в ответе, а незнакомый код просто ничего не находит —
 * и это сказано словами в пустой таблице.
 *
 * **Причина — только у отклонённых.** С состоянием «новый» или «готов» причина
 * не нашла бы ничего по определению: такое сочетание из адреса читается как
 * «без причины», и из адреса оно уходит.
 *
 * **Гипотеза — одна на раздел** (аудит экранов 09.10.2026): лиды, цепочка, очередь
 * и воронка смотрят на одну `?hypothesis=`, и смена вкладки её не теряет — до того
 * у цепочки, очереди и воронки она жила в состоянии вкладки и пропадала при уходе
 * с неё. Остальные фильтры — своей вкладки: у лидов — состояние, причина, поиск
 * и страница, у воронки — период и свои даты (`?period=custom&from=…&to=…`).
 */

import type { LeadsQuery } from '../api/sales';
import { LEAD_STATES, leadReasonTitle } from '../api/salesLabels';
import type { LeadState } from '../api/salesTypes';
import { formatNumber } from '../format';
import { PERIOD_KEYS } from './funnelData';
import type { PeriodKey } from './funnelData';

export type SalesTab = 'leads' | 'hypotheses' | 'kb' | 'sender' | 'chain' | 'queue' | 'funnel';

/** Вкладки раздела. Лиды первыми: с ними работают, гипотезы — сводка; база
 *  знаний и отправитель — то, из чего и от чьего имени пишет агент (срез 3.1);
 *  цепочка — тексты писем, которые уходят лидам (срез 4.6); очередь — сборка
 *  писем из лидов и отправка пачкой (срез 4.6b); воронка — сколько лидов на каждом шаге
 *  от очереди до передачи (срез 5.4). */
export const SALES_TABS: Record<SalesTab, string> = {
  leads: 'Лиды',
  hypotheses: 'Гипотезы',
  kb: 'База знаний',
  sender: 'Отправитель',
  chain: 'Цепочка писем',
  queue: 'Очередь писем',
  funnel: 'Воронка',
};

export const SALES_TAB_KEYS = Object.keys(SALES_TABS) as SalesTab[];
export const LEAD_STATE_KEYS = Object.keys(LEAD_STATES) as LeadState[];

export interface LeadFilters {
  tab: SalesTab;
  /** По адресу, имени, компании или домену. Пусто — не сужать. */
  search: string;
  state: LeadState | null;
  /** Код причины отказа — значение из ответа сервера. */
  reason: string | null;
  /** Номер гипотезы — один на раздел: его видят все вкладки, где выбирают гипотезу. */
  hypothesis: number | null;
  page: number;
  /** Период воронки и свои даты (`YYYY-MM-DD`, пусто — без границы). */
  period: PeriodKey;
  from: string;
  to: string;
}

export const NO_LEAD_FILTERS: LeadFilters = {
  tab: 'leads',
  search: '',
  state: null,
  reason: null,
  hypothesis: null,
  page: 1,
  period: 'all',
  from: '',
  to: '',
};

/** Форма кода причины — как пишет очистка (`duplicate`, `no_mail`). */
const REASON_CODE = /^[a-z_]{1,32}$/;

/** Форма дня — как у поля даты. Сам день проверяет воронка: не дата — сказано под полем. */
const DAY = /^\d{4}-\d{2}-\d{2}$/;

function dayOf(raw: string | null): string {
  return raw !== null && DAY.test(raw) ? raw : '';
}

function known<T extends string>(choices: readonly T[], raw: string | null): T | null {
  return choices.find((choice) => choice === raw) ?? null;
}

/** Номер из адреса — гипотезы или страницы: целое от единицы до `max`, иначе
 *  «не сужать». `12abc`, `1e3`, `-1` и восемь цифр номером не считаются. */
function numberOf(raw: string | null, max: number): number | null {
  const value = raw !== null && /^\d{1,7}$/.test(raw) ? Number(raw) : 0;
  return value >= 1 && value <= max ? value : null;
}

/** Бывает ли причина при этом состоянии: только у отклонённых и у «всех». */
export function reasonOn(state: LeadState | null): boolean {
  return state === null || state === 'rejected';
}

/** Фильтры без того, чего при этом состоянии не бывает. */
function fitted(filters: LeadFilters): LeadFilters {
  return { ...filters, reason: reasonOn(filters.state) ? filters.reason : null };
}

export function readLeadFilters(params: URLSearchParams): LeadFilters {
  const tab = known(SALES_TAB_KEYS, params.get('tab')) ?? 'leads';
  const reason = params.get('reason');
  return fitted({
    tab,
    search: (params.get('search') ?? '').trim(),
    state: known(LEAD_STATE_KEYS, params.get('state')),
    reason: reason !== null && REASON_CODE.test(reason) ? reason : null,
    hypothesis: numberOf(params.get('hypothesis'), 9_999_999),
    page: numberOf(params.get('page'), 1_000_000) ?? 1,
    period: known(PERIOD_KEYS, params.get('period')) ?? 'all',
    from: dayOf(params.get('from')),
    to: dayOf(params.get('to')),
  });
}

/** Фильтры → запрос к серверу. Имена те же, что у адреса экрана. */
export function queryOf(filters: LeadFilters): LeadsQuery {
  const fit = fitted(filters);
  return {
    ...(fit.state !== null ? { state: fit.state } : {}),
    ...(fit.reason !== null ? { reason: fit.reason } : {}),
    ...(fit.hypothesis !== null ? { hypothesis: fit.hypothesis } : {}),
    ...(fit.search !== '' ? { search: fit.search } : {}),
    ...(fit.page > 1 ? { page: fit.page } : {}),
  };
}

/** Фильтры → адрес. Умолчания не пишутся: первая вкладка без `tab`, первая
 *  страница без `page`, всё время без `period`. На других вкладках фильтры лидов
 *  ничего не значат и в адрес не идут — как у диалогов; гипотеза идёт везде. */
export function writeLeadFilters(filters: LeadFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.tab === 'leads') {
    for (const [key, value] of Object.entries(queryOf(filters))) {
      params.set(key, String(value));
    }
    return params;
  }
  params.set('tab', filters.tab);
  if (filters.hypothesis !== null) params.set('hypothesis', String(filters.hypothesis));
  if (filters.tab === 'funnel' && filters.period !== 'all') {
    params.set('period', filters.period);
    if (filters.period === 'custom' && filters.from !== '') params.set('from', filters.from);
    if (filters.period === 'custom' && filters.to !== '') params.set('to', filters.to);
  }
  return params;
}

/** Лиды под фильтром — адресом экрана лидов, тем же, что пишет он сам. По нему ведут
 *  числа гипотез и плитки сводки: число — вход в список, где с ним работают. */
export function leadsLink(hypothesis: number | null, state: LeadState | null): string {
  return `/sales?${writeLeadFilters({ ...NO_LEAD_FILTERS, state, hypothesis }).toString()}`;
}

/** Сужает ли что-нибудь, кроме вкладки и страницы. */
export function isLeadFiltered(filters: LeadFilters): boolean {
  const fit = fitted(filters);
  return fit.search !== '' || fit.state !== null || fit.reason !== null || fit.hypothesis !== null;
}

/** Что сужает таблицу — словами: «ничего не нашлось» без условий читается
 *  как «лидов нет». Имя гипотезы даёт экран: он знает список. */
export function conditionsOf(
  filters: LeadFilters,
  hypothesisName: (id: number) => string | undefined,
): string[] {
  const fit = fitted(filters);
  const said: string[] = [];
  if (fit.search !== '') said.push(`«${fit.search}» в адресе, имени, компании или домене`);
  if (fit.state !== null) said.push(`состояние «${LEAD_STATES[fit.state].title}»`);
  if (fit.reason !== null) said.push(`причина «${leadReasonTitle(fit.reason)}»`);
  if (fit.hypothesis !== null) {
    const name = hypothesisName(fit.hypothesis);
    said.push(name === undefined ? `гипотеза №${fit.hypothesis}` : `гипотеза «${name}»`);
  }
  return said;
}

export interface Emptiness {
  title: string;
  detail: string;
  /** Что предложить кнопкой: сбросить фильтры. Загрузка базы — кнопкой в строке
   *  вкладок, а не второй такой же под таблицей: одно действие — одна кнопка. */
  action: 'reset' | null;
}

/**
 * Почему таблица пуста — словами. Три случая читаются по-разному: лидов нет
 * вовсе — звать загрузку словами; пусто под сочетанием условий — условия названы;
 * пуста страница за концом — сказано, сколько всего.
 */
export function emptinessOf(
  filters: LeadFilters,
  total: number,
  hypothesisName: (id: number) => string | undefined,
): Emptiness {
  const resettable = isLeadFiltered(filters);
  if (total === 0) {
    return {
      title: 'Лидов пока нет.',
      detail:
        'Загрузите базу — файл CSV или Google-таблицу: мастер покажет, что получится, до записи.' +
        (resettable ? ' Фильтры тут ни при чём.' : ''),
      action: null,
    };
  }
  const conditions = conditionsOf(filters, hypothesisName);
  return {
    title: 'Под фильтр ничего не попало.',
    detail:
      conditions.length === 0
        ? `На этой странице пусто. Всего лидов — ${formatNumber(total)}.`
        : `Условия: ${conditions.join(', ')}. Всего лидов — ${formatNumber(total)}.`,
    action: resettable ? 'reset' : null,
  };
}
