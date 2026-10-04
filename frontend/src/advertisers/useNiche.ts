/**
 * Данные карточки бизнесов ниши: очередь, решение человека, сбор из прогона.
 */

import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { collectNiche, decideNiche, fetchNiche } from '../api/niche';

const NICHE_KEY = ['advertisers-niche'] as const;

/** Очередь и решение по ней: после решения очередь перечитывается. */
export function useNicheQueue() {
  const queryClient = useQueryClient();
  const queue = useQuery({ queryKey: NICHE_KEY, queryFn: fetchNiche });
  const decide = useMutation({
    mutationFn: ({ id, write }: { id: number; write: boolean }) => decideNiche(id, write),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: NICHE_KEY }),
    onError: (failure) =>
      notifications.show({
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
      notifications.show({
        color: 'green',
        message: `Прогон №${report.run_id}: бизнесов ниши ${report.found}, новых ${report.added}.`,
      });
    },
    onError: (failure) =>
      notifications.show({ color: 'red', title: 'Не собрали', message: refusalOf(failure) }),
  });
}
