/**
 * Список диалогов с фильтрами из адреса — один на оба вида: таблицу (`ThreadsPage`)
 * и колонку списка рядом с открытым диалогом (`ThreadsSplit`). Два вида одного
 * списка не должны расходиться в том, что считают найденным и как называют пустоту.
 */

import { useQuery } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

import { threadState } from '../api/labels';
import { listThreads } from '../api/outreach';
import type { ThreadCard, ThreadState } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { useTyped } from '../donors/useTyped';
import { formatNumber } from '../format';
import {
  NO_THREAD_FILTERS,
  readThreadFilters,
  threadEmptiness,
  threadMatches,
  THREAD_STATE_KEYS,
  writeThreadFilters,
} from './threadFilters';
import type { ThreadFilters } from './threadFilters';

export const THREADS_QUERY_KEY = ['threads'] as const;

const sameSearch = (draft: string, committed: string) => draft.trim() === committed;

/** Состояния, в которых диалог ждёт человека: разобрать ответ, взять лида. */
const WAITS_FOR_PERSON: ReadonlySet<ThreadState> = new Set<ThreadState>([
  'needs_review',
  'lead',
  'sales_pending',
]);

/** Ждёт ли диалог человека — разобрать ответ, взять лида. */
export function waitsForPerson(state: ThreadState): boolean {
  return WAITS_FOR_PERSON.has(state);
}

const when = (moment: string | null) => (moment === null ? 0 : Date.parse(moment) || 0);

/**
 * Порядок списка: сначала ждущие человека, потом по последнему событию, новые выше.
 * До 09.10.2026 список шёл по номеру диалога, и ответ, ждущий разбора, стоял
 * восемнадцатым из двадцати четырёх (аудит экранов): ради ждущих экран и открывают.
 */
export function byAttention(a: ThreadCard, b: ThreadCard): number {
  const waits = Number(WAITS_FOR_PERSON.has(b.state)) - Number(WAITS_FOR_PERSON.has(a.state));
  if (waits !== 0) return waits;
  return when(b.last_event_at) - when(a.last_event_at) || b.id - a.id;
}

/** «Ждёт разбора · 1»: точка, как у фильтров доноров. */
export function counted(title: string, count: number): string {
  return `${title} · ${formatNumber(count)}`;
}

/** Все диалоги в порядке экрана — для тех, кому фильтры не нужны (`ThreadsIntro`). */
export function useSortedThreads() {
  const query = useQuery({ queryKey: THREADS_QUERY_KEY, queryFn: listThreads });
  const threads = useMemo(() => [...(query.data ?? [])].sort(byAttention), [query.data]);
  return { query, threads };
}

export type ThreadListState = ReturnType<typeof useThreadList>;

export function useThreadList() {
  const [params, setParams] = useSearchParams();
  // Без права «Продажи» состояние продаж из адреса не сужает (П2, `readThreadFilters`).
  const sales = useSession().can('sales');
  const filters = useMemo(() => readThreadFilters(params, sales), [params, sales]);

  // Смена фильтра — замена записи в истории, а не новая: «назад» ведёт туда,
  // откуда пришли, а не по буквам поиска.
  const apply = useCallback(
    (patch: Partial<ThreadFilters>) =>
      setParams(
        (current) => writeThreadFilters({ ...readThreadFilters(current, sales), ...patch }),
        { replace: true },
      ),
    [setParams, sales],
  );
  // Поиск печатают: в адрес он уходит после паузы в наборе, а список
  // сужается сразу — фильтр здесь по уже пришедшим строкам, сервер не ждут.
  const [search, setSearch] = useTyped(
    filters.search,
    (value) => apply({ search: value.trim() }),
    sameSearch,
  );

  // Новый массив на каждую отрисовку пересчитывал бы счётчики всегда: порядок и
  // список — один раз на ответ сервера (`useSortedThreads`).
  const { query, threads } = useSortedThreads();
  const counts = useMemo(() => {
    const totals = new Map<ThreadState, number>();
    for (const thread of threads) {
      totals.set(thread.state, (totals.get(thread.state) ?? 0) + 1);
    }
    return totals;
  }, [threads]);

  const typed = { ...filters, search: search.trim() };
  const shown = threads.filter((thread) => threadMatches(thread, typed));
  // В списке — состояния, которые есть, и выбранное, даже если его нет:
  // иначе фильтр из адреса («лиды», когда лидов ноль) стоял бы пустым.
  const states = THREAD_STATE_KEYS.filter(
    (state) => (counts.get(state) ?? 0) > 0 || state === filters.state,
  );
  const empty = shown.length === 0 ? threadEmptiness(typed, counts, threads.length) : null;
  // Пункты фильтра состояния — с числами, «все» первым.
  const stateOptions = [
    { value: 'all', label: counted('все', threads.length) },
    ...states.map((state) => ({
      value: state,
      label: counted(threadState(state).title, counts.get(state) ?? 0),
    })),
  ];
  const pickState = (value: string | null) =>
    apply({ state: THREAD_STATE_KEYS.find((state) => state === value) ?? null });
  const reset = () => {
    setSearch('');
    setParams(writeThreadFilters(NO_THREAD_FILTERS), { replace: true });
  };

  return {
    query,
    threads,
    filters,
    typed,
    search,
    setSearch,
    shown,
    empty,
    stateOptions,
    pickState,
    reset,
  };
}
