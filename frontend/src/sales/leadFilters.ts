/**
 * Вкладка, фильтры лидов и страница — в адресе раздела «Продажи».
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
 */

import type { LeadsQuery } from '../api/sales';
import { LEAD_STATES, leadReasonTitle } from '../api/salesLabels';
import type { LeadState } from '../api/salesTypes';
import { formatNumber } from '../format';

export type SalesTab = 'leads' | 'hypotheses' | 'kb' | 'sender' | 'chain' | 'queue';

/** Вкладки раздела. Лиды первыми: с ними работают, гипотезы — сводка; база
 *  знаний и отправитель — то, из чего и от чьего имени пишет агент (срез 3.1);
 *  цепочка — тексты писем, которые уходят лидам (срез 4.6); очередь — сборка
 *  писем из лидов и отправка пачкой (срез 4.6b). */
export const SALES_TABS: Record<SalesTab, string> = {
  leads: 'Лиды',
  hypotheses: 'Гипотезы',
  kb: 'База знаний',
  sender: 'Отправитель',
  chain: 'Цепочка писем',
  queue: 'Очередь писем',
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
  /** Номер гипотезы. */
  hypothesis: number | null;
  page: number;
}

export const NO_LEAD_FILTERS: LeadFilters = {
  tab: 'leads',
  search: '',
  state: null,
  reason: null,
  hypothesis: null,
  page: 1,
};

/** Форма кода причины — как пишет очистка (`duplicate`, `no_mail`). */
const REASON_CODE = /^[a-z_]{1,32}$/;

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
 *  страница без `page`. На других вкладках фильтры лидов ничего не значат
 *  и в адрес не идут — как у диалогов. */
export function writeLeadFilters(filters: LeadFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.tab !== 'leads') {
    params.set('tab', filters.tab);
    return params;
  }
  for (const [key, value] of Object.entries(queryOf(filters))) {
    params.set(key, String(value));
  }
  return params;
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
  /** Что предложить: сбросить фильтры — или загрузить базу, если лидов нет вовсе. */
  action: 'reset' | 'import' | null;
}

/**
 * Почему таблица пуста — словами. Три случая читаются по-разному: лидов нет
 * вовсе — звать загрузку; пусто под сочетанием условий — условия названы;
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
      action: 'import',
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
