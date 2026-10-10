/**
 * Данные карточки бизнесов ниши: очередь, решение человека, сбор из прогона.
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { collectNiche, decideNiche, fetchNiche } from '../api/niche';
import { notify } from '../notices';

const NICHE_KEY = ['advertisers-niche'] as const;

/** Страница очереди и решение по ней: после решения очередь перечитывается. */
export function useNicheQueue(page: number) {
  const queryClient = useQueryClient();
  const queue = useQuery({
    queryKey: [...NICHE_KEY, page],
    queryFn: () => fetchNiche(page),
    // Пока едет следующая страница, стоит прежняя: пустой список на долю секунды
    // читался бы как «ждущих нет».
    placeholderData: keepPreviousData,
  });
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
  return { queue, decide };
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
