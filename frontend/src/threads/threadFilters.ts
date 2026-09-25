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
 * Правило то же, что у доноров (`donors/donorFilters.ts`).
 */

import { THREAD_STATES } from '../api/labels';
import type { ThreadCard, ThreadState } from '../api/types';

export interface ThreadFilters {
  state: ThreadState | null;
  /** По донору или адресу. Пусто — не сужать. */
  search: string;
}

export const NO_THREAD_FILTERS: ThreadFilters = { state: null, search: '' };

export const THREAD_STATE_KEYS = Object.keys(THREAD_STATES) as ThreadState[];

export function readThreadFilters(params: URLSearchParams): ThreadFilters {
  const state = params.get('state');
  return {
    state: THREAD_STATE_KEYS.find((known) => known === state) ?? null,
    search: (params.get('search') ?? '').trim(),
  };
}

/** Фильтры → адрес. Пустое не пишется: `?state=` ничего не сужает. */
export function writeThreadFilters(filters: ThreadFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.state !== null) params.set('state', filters.state);
  if (filters.search !== '') params.set('search', filters.search);
  return params;
}

export function isThreadFiltered(filters: ThreadFilters): boolean {
  return filters.state !== null || filters.search !== '';
}

/** Подходит ли диалог. Поиск — по донору и по адресу, без учёта регистра. */
export function threadMatches(thread: ThreadCard, filters: ThreadFilters): boolean {
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
      detail: 'Диалог заводится вместе с письмом, когда собирают очередь на экране «Письма».',
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
