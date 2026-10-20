/**
 * Резерв на экране — данные и действие: страница резерва под фильтрами из адреса и «Взять
 * на рассмотрение». Отдельно от разметки (`ReservePage.tsx`): экран — заголовок, таблица и
 * страницы, а здесь — что он спрашивает у сервера и что говорит, когда пусто.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import type { Emptiness } from '../components/WholeRow';
import { usePagedQuery } from '../components/useUrlFilters';
import { notify } from '../notices';
import { loadReserve, takeFromReserve } from './reserveApi';
import type { ReserveCard } from './reserveApi';
import { isFiltered, writeReserve } from './reserveFilters';
import type { ReserveFilters } from './reserveFilters';
import { takeOutcome } from './reserveWords';

/** Почему пусто — словами: под фильтрами, у прогона или во всём резерве. */
function emptinessOf(filters: ReserveFilters): Emptiness {
  if (filters.run !== null && !isFiltered({ ...filters, run: null })) {
    return {
      title: `В резерве прогона №${filters.run} никого`,
      detail:
        'Все домены прогона прошли или сняты гейтом не по «кому не пишем». Прогон до 20.10.2026 вердикт по доменам не записывал — его резерва здесь нет.',
      resettable: false,
      path: { label: 'Весь резерв', to: '/donors/reserve' },
    };
  }
  if (isFiltered(filters)) {
    return {
      title: 'Под этими фильтрами в резерве никого',
      detail: 'Снимите фильтр причины или страны — или откройте весь резерв.',
      resettable: true,
    };
  }
  return {
    title: 'Резерв пуст',
    detail:
      'Сюда попадают домены прогонов, которые не прошли пороги, не проверены из-за нехватки данных или «кому не пишем», — с прогонов от 20.10.2026.',
    resettable: false,
  };
}

/** «Взять на рассмотрение»: уведомление словами, резерв, очередь и история — заново. */
export function useTake() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (row: ReserveCard) =>
      takeFromReserve([{ run_id: row.run_id, domain_id: row.domain_id }]),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['reserve'] });
      await queryClient.invalidateQueries({ queryKey: ['review'] });
      await queryClient.invalidateQueries({ queryKey: ['runs'] });
      notify({ color: result.taken > 0 ? 'green' : 'yellow', message: takeOutcome(result) });
    },
    onError: (failure) =>
      notify({ color: 'red', title: 'Не взяли на рассмотрение', message: refusalOf(failure) }),
  });
}

/** Ответ на резерв под этими фильтрами — и всё, что экран из него выводит: страницы,
 *  приглушённые прежние строки, отказ и «пусто и почему». Страница за концом — последняя. */
export function useReserve(
  filters: ReserveFilters,
  turn: (page: number, replace?: boolean) => void,
) {
  const { query, data, settled, pages } = usePagedQuery(
    ['reserve', writeReserve(filters).toString()],
    () => loadReserve(filters),
    filters.page,
    turn,
  );
  return {
    data,
    pages,
    loading: data === undefined && query.isPending,
    stale: query.isPlaceholderData && query.isFetching,
    refusal: query.error ? refusalOf(query.error) : null,
    empty: settled && data?.rows.length === 0 ? emptinessOf(filters) : null,
  };
}
