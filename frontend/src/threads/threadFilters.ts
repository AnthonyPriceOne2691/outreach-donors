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
 */

import { THREAD_STATES } from '../api/labels';
import { PART_PARAM, partOf, stateShown } from '../api/stages';
import type { Part } from '../api/stages';
import type { ThreadCard, ThreadState } from '../api/types';

export interface ThreadFilters {
  /** Часть: `sales` — только переписки продаж; пусто — доноры и рекламодатели. */
  part: Part | null;
  state: ThreadState | null;
  /** По донору или адресу. Пусто — не сужать. */
  search: string;
}

export const NO_THREAD_FILTERS: ThreadFilters = { part: null, state: null, search: '' };

export const THREAD_STATE_KEYS = Object.keys(THREAD_STATES) as ThreadState[];

/** Фильтры из адреса. `sales` — `can('sales')`: без права состояния и часть продаж не сужают. */
export function readThreadFilters(params: URLSearchParams, sales: boolean): ThreadFilters {
  const state = params.get('state');
  return {
    part: sales ? partOf(params) : null,
    state: THREAD_STATE_KEYS.find((known) => known === state && stateShown(known, sales)) ?? null,
    search: (params.get('search') ?? '').trim(),
  };
}

/** Фильтры → адрес. Пустое не пишется: `?state=` ничего не сужает. */
export function writeThreadFilters(filters: ThreadFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.part !== null) params.set(PART_PARAM, filters.part);
  if (filters.state !== null) params.set('state', filters.state);
  if (filters.search !== '') params.set('search', filters.search);
  return params;
}

export function isThreadFiltered(filters: ThreadFilters): boolean {
  return filters.state !== null || filters.search !== '';
}

/** Диалог — в части списка: переписка продаж — в «Диалогах продаж», остальные — в «Диалогах». */
export function inPart(thread: ThreadCard, part: Part | null): boolean {
  return (thread.stage === 'sales') === (part === 'sales');
}

/** Подходит ли диалог. Поиск — по донору и по адресу, без учёта регистра. */
export function threadMatches(thread: ThreadCard, filters: ThreadFilters): boolean {
  if (!inPart(thread, filters.part)) return false;
  if (filters.state !== null && thread.state !== filters.state) return false;
  const needle = filters.search.trim().toLowerCase();
  if (needle === '') return true;
  return (
    thread.host.toLowerCase().includes(needle) ||
    (thread.contact_email ?? '').toLowerCase().includes(needle)
  );
}

/** Почему под фильтром пусто — словами. Пустой экран объясняет, чего на нём
 *  нет: «ничего не нашлось» без условий читается как «диалогов нет вовсе». */
export function threadEmptiness(
  filters: ThreadFilters,
  counts: Map<ThreadState, number>,
  total: number,
): { title: string; detail: string } {
  if (total === 0) {
    return {
      title: 'Диалогов ещё нет.',
      detail:
        filters.part === 'sales'
          ? 'Переписка продаж заводится с первым письмом лиду — очередь писем собирают в разделе «Продажи».'
          : 'Диалог заводится вместе с письмом, когда собирают очередь на экране «Письма».',
    };
  }
  if (filters.state !== null && (counts.get(filters.state) ?? 0) === 0) {
    return {
      title: `В состоянии «${THREAD_STATES[filters.state].title}» диалогов нет.`,
      detail: `Нет во всём списке, а не только под поиском. Всего диалогов — ${total}.`,
    };
  }
  const said: string[] = [];
  if (filters.state !== null) said.push(`состояние «${THREAD_STATES[filters.state].title}»`);
  if (filters.search !== '') said.push(`«${filters.search}» в доноре или адресе`);
  return {
    title: 'Под фильтр ничего не попало.',
    detail: `Условия: ${said.join(', ')}. Всего диалогов — ${total}.`,
  };
}
