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
 * с неё. Остальные фильтры — своей вкладки: у лидов — состояние, причина, поиск, период
 * добавления (`?added_from=…&added_to=…`, днями, `leadPeriod.ts`) и страница, у воронки —
 * период и свои даты (`?period=custom&from=…&to=…`), у отчёта —
 * неделя (`?tab=report&week=…`, её понедельник), у качества агента — версия агента
 * (`?cohort=…`, номер сервера; пусто — текущая), у сбора базы — выбранный прогон
 * (`?tab=collect&run=N`: «назад» и ссылка открывают тот же прогон, тест прогона 14.10).
 */

import type { LeadsQuery } from '../api/sales';
import { LEAD_STATES, leadReasonTitle } from '../api/salesLabels';
import type { LeadState } from '../api/salesTypes';
import { formatNumber } from '../format';
import { PERIOD_KEYS } from './funnelData';
import type { PeriodKey } from './funnelData';
import { ADDED_KEYS, addedDayOf, addedProblem, addedQuery, addedWords } from './leadPeriod';
import { COHORT_ID } from './qualityData';

export type SalesTab =
  | 'leads'
  | 'hypotheses'
  | 'collect'
  | 'kb'
  | 'sender'
  | 'stoplist'
  | 'chain'
  | 'queue'
  | 'mailing'
  | 'funnel'
  | 'report'
  | 'agent';

/** Вкладки раздела — в порядке работы (слово владельца 13.10.2026). Сначала рабочие: кому
 *  и зачем пишем (гипотезы), откуда база (сбор новой по ключам — 6.1, и лиды), что уходит
 *  лиду (цепочка писем, срез 4.6), сборка и отправка пачкой (очередь, 4.6b), когда и сколько
 *  уйдёт (рассылка: прогноз отправки, круг 22 — рядом с очередью, чей срок он считает), что вышло
 *  (воронка, 5.4, и недельный отчёт, 6.3), как люди обходятся с черновиками агента (качество
 *  агента, 6.4). Затем настройки — в порядке первого запуска: от чьего имени пишем
 *  (отправитель: без него письма не уходят), из чего пишет агент (база знаний, 3.1) и кому
 *  продажи не пишут (стоп-лист). Открывается раздел по-прежнему на лидах (`tab: 'leads'`).
 *  Порядок один у вкладок и у списка «Раздел» на телефоне. Вкладка лидов называется «emails» —
 *  ровно так, строчными латиницей (слово владельца 20.10.2026); ключ в коде и в адресе прежний. */
export const SALES_TABS: Record<SalesTab, string> = {
  hypotheses: 'Гипотезы',
  collect: 'Сбор базы',
  leads: 'emails',
  chain: 'Цепочка писем',
  queue: 'Очередь писем',
  mailing: 'Рассылка',
  funnel: 'Воронка',
  report: 'Отчёт',
  agent: 'Агент: качество',
  sender: 'Отправитель',
  kb: 'База знаний',
  stoplist: 'Стоп-лист',
};

/** Настройки раздела — в списке «Раздел» своей группой под чертой. */
export const SETTINGS_TABS: ReadonlySet<SalesTab> = new Set(['kb', 'sender', 'stoplist']);

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
  /** Период добавления лида — дни `YYYY-MM-DD`, оба включительно; пусто — без границы. */
  addedFrom: string;
  addedTo: string;
  page: number;
  /** Период воронки и свои даты (`YYYY-MM-DD`, пусто — без границы). */
  period: PeriodKey;
  from: string;
  to: string;
  /** Версия агента на вкладке качества — номер сервера; пусто — текущая. */
  cohort: string;
  /** Неделя отчёта — её понедельник (`YYYY-MM-DD`); пусто — прошлая неделя. */
  week: string;
  /** Прогон сбора базы; пусто — последний прогон гипотезы. */
  run: number | null;
}

export const NO_LEAD_FILTERS: LeadFilters = {
  tab: 'leads',
  search: '',
  state: null,
  reason: null,
  hypothesis: null,
  addedFrom: '',
  addedTo: '',
  page: 1,
  period: 'all',
  from: '',
  to: '',
  cohort: '',
  week: '',
  run: null,
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
    addedFrom: addedDayOf(params.get(ADDED_KEYS.from)),
    addedTo: addedDayOf(params.get(ADDED_KEYS.to)),
    page: numberOf(params.get('page'), 1_000_000) ?? 1,
    period: known(PERIOD_KEYS, params.get('period')) ?? 'all',
    from: dayOf(params.get('from')),
    to: dayOf(params.get('to')),
    cohort: cohortOf(params.get('cohort')),
    week: dayOf(params.get('week')),
    run: numberOf(params.get('run'), 9_999_999),
  });
}

/** Номер версии агента из адреса — только той формы, что пишет сервер; иначе «текущая». */
function cohortOf(raw: string | null): string {
  return raw !== null && COHORT_ID.test(raw) ? raw : '';
}

/** Условия под колонками — одними именами в адресе и в запросе к серверу. */
function narrowing(fit: LeadFilters): LeadsQuery {
  return {
    ...(fit.state !== null ? { state: fit.state } : {}),
    ...(fit.reason !== null ? { reason: fit.reason } : {}),
    ...(fit.hypothesis !== null ? { hypothesis: fit.hypothesis } : {}),
    ...(fit.search !== '' ? { search: fit.search } : {}),
  };
}

/** Фильтры → запрос к серверу. Имена условий те же, что у адреса экрана; период добавления —
 *  моментами (`addedQuery`), в адресе он днями. Период «с» позже «по» на сервер не уходит. */
export function queryOf(filters: LeadFilters): LeadsQuery {
  const fit = fitted(filters);
  return {
    ...narrowing(fit),
    ...addedQuery(fit.addedFrom, fit.addedTo),
    ...(fit.page > 1 ? { page: fit.page } : {}),
  };
}

/** Адрес вкладки лидов: условия — именами запроса, период — днями своих ключей, страница. */
function leadParams(filters: LeadFilters): [string, string][] {
  const fit = fitted(filters);
  const found = Object.entries(narrowing(fit)).map(([key, value]): [string, string] => [
    key,
    String(value),
  ]);
  if (fit.addedFrom !== '') found.push([ADDED_KEYS.from, fit.addedFrom]);
  if (fit.addedTo !== '') found.push([ADDED_KEYS.to, fit.addedTo]);
  if (fit.page > 1) found.push(['page', String(fit.page)]);
  return found;
}

/** Фильтры → адрес. Умолчания не пишутся: первая вкладка без `tab`, первая
 *  страница без `page`, всё время без `period`. На других вкладках фильтры лидов
 *  ничего не значат и в адрес не идут — как у диалогов; гипотеза идёт везде. */
export function writeLeadFilters(filters: LeadFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.tab === 'leads') {
    for (const [key, value] of leadParams(filters)) params.set(key, value);
    return params;
  }
  params.set('tab', filters.tab);
  if (filters.hypothesis !== null) params.set('hypothesis', String(filters.hypothesis));
  for (const [key, value] of tabParams(filters)) params.set(key, value);
  return params;
}

/** Свои фильтры вкладки в адресе: у воронки — период и свои даты, у отчёта — неделя, у качества
 *  агента — версия, у сбора базы — прогон. Умолчания (всё время, прошлая неделя, текущая
 *  версия, последний прогон) не пишутся. */
function tabParams(filters: LeadFilters): [string, string][] {
  if (filters.tab === 'funnel') return funnelParams(filters);
  if (filters.tab === 'report') return filters.week === '' ? [] : [['week', filters.week]];
  if (filters.tab === 'collect') return filters.run === null ? [] : [['run', String(filters.run)]];
  return filters.tab === 'agent' && filters.cohort !== '' ? [['cohort', filters.cohort]] : [];
}

/** Период воронки: всё время в адрес не пишется, свои даты — только заданные. */
function funnelParams(filters: LeadFilters): [string, string][] {
  if (filters.period === 'all') return [];
  const found: [string, string][] = [['period', filters.period]];
  if (filters.period === 'custom' && filters.from !== '') found.push(['from', filters.from]);
  if (filters.period === 'custom' && filters.to !== '') found.push(['to', filters.to]);
  return found;
}

/** Лиды под фильтром — адресом экрана лидов, тем же, что пишет он сам. По нему ведут
 *  числа гипотез и плитки сводки: число — вход в список, где с ним работают. */
export function leadsLink(hypothesis: number | null, state: LeadState | null): string {
  return `/sales?${writeLeadFilters({ ...NO_LEAD_FILTERS, state, hypothesis }).toString()}`;
}

/** Вкладка раздела для гипотезы — тем же адресом, что пишет сам раздел: так ведёт вход в
 *  «Цепочку писем» из плашки очереди. */
export function salesTabLink(tab: SalesTab, hypothesis: number | null): string {
  return `/sales?${writeLeadFilters({ ...NO_LEAD_FILTERS, tab, hypothesis }).toString()}`;
}

/** Сужает ли что-нибудь, кроме вкладки и страницы. */
export function isLeadFiltered(filters: LeadFilters): boolean {
  const fit = fitted(filters);
  const added = fit.addedFrom !== '' || fit.addedTo !== '';
  return (
    added ||
    fit.search !== '' ||
    fit.state !== null ||
    fit.reason !== null ||
    fit.hypothesis !== null
  );
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
  const added = addedWords(fit.addedFrom, fit.addedTo);
  if (added !== null) said.push(added);
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

/** Период «с» позже «по» — вместо строк: запроса нет, а пустая таблица без слов читалась бы
 *  «за период никого». `null` — период годится. */
export function backwardsOf(filters: LeadFilters): Emptiness | null {
  if (addedProblem(filters.addedFrom, filters.addedTo) === null) return null;
  return {
    title: 'Период задан наоборот.',
    detail: `«С» позже «по» — такой период пуст. Поменяйте даты местами, и таблица покажет, кого добавили за период.`,
    action: 'reset',
  };
}
