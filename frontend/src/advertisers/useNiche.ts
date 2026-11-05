/**
 * Данные карточки бизнесов ниши: очередь, решение человека, сбор из прогона.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { collectNiche, decideNiche, fetchNiche } from '../api/niche';
import { usePagedQuery } from '../components/useUrlFilters';
import { notify } from '../notices';

const NICHE_KEY = ['advertisers-niche'] as const;

/** Страница очереди и решение по ней: после решения очередь перечитывается. Пока едет следующая
 *  страница, стоит прежняя: пустой список на долю секунды читался бы как «ждущих нет». Страница за
 *  концом — решён последний бизнес последней страницы или открыта старая ссылка: экран уходит на
 *  последнюю, заменяя адрес, а не добавляя (`usePagedQuery`). */
export function useNicheQueue(page: number, turn: (page: number, replace?: boolean) => void) {
  const queryClient = useQueryClient();
  const { query: queue, pages } = usePagedQuery(
    [...NICHE_KEY, page],
    () => fetchNiche(page),
    page,
    turn,
  );
  const decide = useMutation({
    mutationFn: ({ id, write }: { id: number; write: boolean }) => decideNiche(id, write),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: NICHE_KEY }),
    onError: (failure) =>
      notify({
        color: 'red',
        title: 'Не записали решение',
        message: refusalOf(failure),
      }),
  });
  return { queue, pages, decide };
}

/** Сбор из прогона: итог словами, очередь перечитывается. */
export function useNicheCollect() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId: number) => collectNiche(runId),
    onSuccess: async (report) => {
      await queryClient.invalidateQueries({ queryKey: NICHE_KEY });
      notify({
        color: 'green',
        message: `Прогон №${report.run_id}: бизнесов ниши ${report.found}, новых ${report.added}.`,
      });
    },
    onError: (failure) =>
      notify({ color: 'red', title: 'Не собрали', message: refusalOf(failure) }),
  });
}
