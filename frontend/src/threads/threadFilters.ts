/**
 * Фильтры списка диалогов — в адресе экрана.
 *
 * **Адрес, а не состояние компонента.** Главная ведёт сюда плитками —
 * «Разобрать цены» на `?state=needs_review`, «Лиды рекламодателей» на
 * `?state=lead` — и человек должен попасть на отфильтрованный список, а не
 * искать нужный диалог среди сотни. Тот же адрес переживает «назад» из
 * карточки диалога и обновление вкладки.
 *
 * **Адрес — чужой ввод.** Его правят руками и присылают устаревшим, поэтому
 * незнакомое состояние — «не сужать», а не пустой экран без объяснения.
 * Правило то же, что у доноров (`donors/donorFilters.ts`). Состояние продаж
 * без права «Продажи» — тоже «не сужать» (решение Anthony 10.10.2026, П2):
 * переписок продаж в таком списке нет, и фильтр их словами не называет.
 *
 * **Часть списка — тоже в адресе** (просьба Anthony 11.10.2026): «Диалоги» — переписки
 * доноров и рекламодателей, «Диалоги продаж» — тот же экран с `?stage=sales`. Часть — не
 * фильтр, а сам список: «найдено N из M» и «всего» считаются внутри неё. Без права
 * «Продажи» часть продаж из адреса не сужает — её переписок сервер и не отдаёт.
 *
 * **Кому писали — тоже в адресе** (`?role=advertisers`, слово владельца 14.10.2026): в
 * «Диалогах» доноры и рекламодатели шли вперемешку, и письмо рекламодателю в списке не
 * отличалось от письма донору. Переключатель «Все · Доноры · Рекламодатели» сужает список
 * и считается фильтром — «найдено N из M» и «Сбросить фильтры» его видят. В «Диалогах
 * продаж» его нет: там одна сторона, и `role` из адреса там не сужает.
 *
 * **Доноры и рекламодатели — тоже части** (23.10.2026): «Диалоги с донорами» —
 * `?stage=donors`, «Диалоги с рекламодателями» — `?stage=advertisers`, своими пунктами
 * меню. Переключатель «Кому писали» остался только у списка без части — прежнего адреса
 * `/threads`, которого в меню больше нет.
 */

import { THREAD_STATES } from '../api/labels';
import { PART_PARAM, partOf, stageShown, stateShown } from '../api/stages';
import type { Part, Stage } from '../api/stages';
import type { ThreadCard, ThreadState } from '../api/types';

/** Кому писали: донору о цене или рекламодателю оффер. */
export type ThreadRole = Exclude<Stage, 'sales'>;

/** Параметр адреса переключателя «Кому писали». */
export const ROLE_PARAM = 'role';

/** Список части — заголовком экрана и пунктом меню (`layout/sections.ts`): одни слова. */
export const THREADS_TITLES: Record<Part, string> = {
  donors: 'Диалоги с донорами',
  advertisers: 'Диалоги с рекламодателями',
  sales: 'Диалоги продаж',
};

/** Подписи переключателя — в том же порядке, что «Кому письма» на «Письмах». */
export const THREAD_ROLES: Record<ThreadRole, string> = {
  donors: 'Доноры',
  advertisers: 'Рекламодатели',
};

const ROLE_KEYS = Object.keys(THREAD_ROLES) as ThreadRole[];

/** Сторона из адреса или переключателя; «все» и незнакомое — не сужать. */
export function roleOf(value: string | null): ThreadRole | null {
  return ROLE_KEYS.find((known) => known === value) ?? null;
}

export interface ThreadFilters {
  /** Часть: переписки одного этапа — доноров, рекламодателей или продаж; пусто — доноры
   *  и рекламодатели вместе. */
  part: Part | null;
  /** Кому писали — только в «Диалогах», без части. Пусто — и донорам, и рекламодателям. */
  role: ThreadRole | null;
  state: ThreadState | null;
  /** По донору или адресу. Пусто — не сужать. */
  search: string;
}

export const NO_THREAD_FILTERS: ThreadFilters = { part: null, role: null, state: null, search: '' };

export const THREAD_STATE_KEYS = Object.keys(THREAD_STATES) as ThreadState[];

/** Фильтры из адреса. `sales` — `can('sales')`: без права состояния и часть продаж не сужают. */
export function readThreadFilters(params: URLSearchParams, sales: boolean): ThreadFilters {
  const state = params.get('state');
  const named = partOf(params);
  const part = named !== null && stageShown(named, sales) ? named : null;
  return {
    part,
    role: part === null ? roleOf(params.get(ROLE_PARAM)) : null,
    state: THREAD_STATE_KEYS.find((known) => known === state && stateShown(known, sales)) ?? null,
    search: (params.get('search') ?? '').trim(),
  };
}

/** Фильтры → адрес. Пустое не пишется: `?state=` ничего не сужает. */
export function writeThreadFilters(filters: ThreadFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.part !== null) params.set(PART_PARAM, filters.part);
  if (filters.role !== null) params.set(ROLE_PARAM, filters.role);
  if (filters.state !== null) params.set('state', filters.state);
  if (filters.search !== '') params.set('search', filters.search);
  return params;
}

export function isThreadFiltered(filters: ThreadFilters): boolean {
  return filters.role !== null || filters.state !== null || filters.search !== '';
}

/** Диалог — в части списка: своего этапа; без части — доноров и рекламодателей. */
export function inPart(thread: ThreadCard, part: Part | null): boolean {
  return part === null ? thread.stage !== 'sales' : thread.stage === part;
}

/** Лиды файлом — ответы рекламодателей на оффер: у списка с рекламодателями. */
export function hasLeads(part: Part | null): boolean {
  return part === null || part === 'advertisers';
}

/** Диалог — той стороны, что выбрана: часть списка и «Кому писали». Одно правило у
 *  списка и у «Ждут человека — открыть первый» (`ThreadsIntro`): кнопка рядом со списком
 *  рекламодателей не ведёт к донору. Состояние и поиск сюда не входят — это фильтры. */
export function onSide(thread: ThreadCard, side: Pick<ThreadFilters, 'part' | 'role'>): boolean {
  return inPart(thread, side.part) && (side.role === null || thread.stage === side.role);
}

/** Подходит ли диалог. Поиск — по донору и по адресу, без учёта регистра. */
export function threadMatches(thread: ThreadCard, filters: ThreadFilters): boolean {
  if (!onSide(thread, filters)) return false;
  if (filters.state !== null && thread.state !== filters.state) return false;
  const needle = filters.search.trim().toLowerCase();
  if (needle === '') return true;
  return (
    thread.host.toLowerCase().includes(needle) ||
    (thread.contact_email ?? '').toLowerCase().includes(needle)
  );
}

/** Где заводится переписка части: пустой список называет экран, где её начинают. */
const STARTS: Record<Part | 'all', string> = {
  donors: 'Диалог заводится вместе с письмом, когда собирают очередь на экране «Письма донорам».',
  advertisers:
    'Диалог заводится вместе с письмом, когда собирают очередь на экране «Письма рекламодателям».',
  sales:
    'Переписка продаж заводится с первым письмом лиду — очередь писем собирают в разделе «Продажи».',
  all: 'Диалог заводится вместе с письмом, когда собирают очередь на экранах «Письма донорам» и «Письма рекламодателям».',
};

/** Почему под фильтром пусто — словами. Пустой экран объясняет, чего на нём
 *  нет: «ничего не нашлось» без условий читается как «диалогов нет вовсе». */
export function threadEmptiness(
  filters: ThreadFilters,
  counts: Map<ThreadState, number>,
  total: number,
): { title: string; detail: string } {
  if (total === 0) {
    return { title: 'Диалогов ещё нет.', detail: STARTS[filters.part ?? 'all'] };
  }
  if (filters.state !== null && (counts.get(filters.state) ?? 0) === 0) {
    return {
      title: `В состоянии «${THREAD_STATES[filters.state].title}» диалогов нет.`,
      detail: `Нет во всём списке, а не только под поиском. Всего диалогов — ${total}.`,
    };
  }
  const said: string[] = [];
  if (filters.role !== null) said.push(`только ${THREAD_ROLES[filters.role].toLowerCase()}`);
  if (filters.state !== null) said.push(`состояние «${THREAD_STATES[filters.state].title}»`);
  if (filters.search !== '') said.push(`«${filters.search}» в доноре или адресе`);
  return {
    title: 'Под фильтр ничего не попало.',
    detail: `Условия: ${said.join(', ')}. Всего диалогов — ${total}.`,
  };
}
