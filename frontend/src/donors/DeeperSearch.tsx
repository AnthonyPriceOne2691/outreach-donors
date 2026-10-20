/**
 * «Искать глубже» — донору с исходом «адреса нет»: в его карточке и в строке списка.
 *
 * Обычный поиск экономит на каждом из сотен доменов, и его «адреса нет» — это «не нашли
 * за этот бюджет». Глубже — та же лестница для одного донора: больше страниц, разделы из
 * карты сайта, браузер, если он есть на сервере; платный сервис не зовётся. Обещать
 * больше экран не должен: замер 20.10.2026 на двенадцати донорах «адреса нет» дал один
 * адрес, и тот — браузером. Сайт, который не пустил нас проверкой «вы не робот» или
 * закрыт для страны сервера, глубже тоже не откроется — так и сказано у кнопки.
 *
 * **Можно ли — решает сервер** (`deeper.refusal`, `row.deeper`), одним правилом с самой
 * задачей. Кнопки нет, когда нельзя; почему — в карточке: «Глубже уже искали …».
 *
 * **Задача переживает уход с экрана**, как у «Найти адрес»: её номер помнится по донору,
 * и строка списка и карточка ведут одну и ту же задачу.
 */

import { ActionIcon, Button, Loader, Stack, Text, Tooltip } from '@mantine/core';
import { IconSearch } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { refusalOf } from '../api/client';
import { searchDonorDeeper } from '../api/deeper';
import type { DeeperCard, DonorCardWithReach, DonorRowWithReach } from '../api/deeper';
import { fetchJob } from '../api/jobs';
import type { JobState } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatDate } from '../format';
import { JobLine, jobRestarted } from '../jobs/JobLine';
import { notify } from '../notices';
import { forget, remember, remembered } from '../storage';
import { CONTACTS_QUERY_KEY } from './PendingContacts';

const ACTIVE: ReadonlySet<JobState> = new Set(['queued', 'running', 'retry_wait']);

/** Что делает кнопка — одной фразой у неё, без обещаний сверх замеренного. */
export const DEEPER_HINT =
  'Глубже: больше страниц сайта, разделы из его карты и браузер, если он есть на сервере; ' +
  'платный сервис не зовётся. Сайт, который не пустил нас проверкой «вы не робот» или ' +
  'закрыт для страны сервера, так обычно не открывается.';

function jobKey(donorId: number): string {
  return `donor:${donorId}:deeper-job`;
}

/** Итог задачи словами сервера: что нашлось и где, или почему нет. */
function describe(report: Record<string, unknown>): string | null {
  const note = typeof report.note === 'string' ? report.note : '';
  return note ? `${note.charAt(0).toUpperCase()}${note.slice(1)}.` : null;
}

/** Поиск глубже одного донора: постановка, задача и её конец. Конец ловит сам хук, а не
 *  строка задачи: в строке списка строке задачи нет места, а перечитать список надо. */
function useDeeper(donorId: number) {
  const queryClient = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(() => remembered(jobKey(donorId)));
  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => fetchJob(jobId ?? ''),
    enabled: jobId !== null,
    retry: false,
    refetchInterval: (query) =>
      query.state.data === undefined || ACTIVE.has(query.state.data.state) ? 3_000 : false,
  });
  const state = job.data?.state;
  const running = state !== undefined && ACTIVE.has(state);

  const start = useMutation({
    mutationFn: () => searchDonorDeeper(donorId),
    onSuccess: async (queued) => {
      remember(jobKey(donorId), queued.job_id);
      setJobId(queued.job_id);
      // Номер у задачи постоянный — на донора: прежний итог под ним надо перечитать.
      await jobRestarted(queryClient, queued.job_id);
      notify({ message: 'Поиск глубже поставлен в очередь', color: 'green' });
    },
    onError: (failure) =>
      notify({ title: 'Не поставили', message: refusalOf(failure), color: 'red' }),
  });

  // Кончилась задача — перечитать карточку, список и число ждущих: у донора мог
  // появиться адрес или форма, и итог глубже записан у него самого. Один раз на задачу.
  const told = useRef<string | null>(null);
  useEffect(() => {
    if (jobId === null || state === undefined) return;
    if (ACTIVE.has(state)) {
      told.current = null;
      return;
    }
    if (told.current === jobId) return;
    told.current = jobId;
    if (state === 'done') forget(jobKey(donorId));
    void queryClient.invalidateQueries({ queryKey: ['donor', String(donorId)] });
    void queryClient.invalidateQueries({ queryKey: ['donors'] });
    void queryClient.invalidateQueries({ queryKey: CONTACTS_QUERY_KEY });
  }, [queryClient, donorId, jobId, state]);

  return { jobId, running, start };
}

/** Итог прошлого поиска глубже — словами сервера. */
function DeeperPast({ deeper }: { deeper: DeeperCard }) {
  if (deeper.at === null) return null;
  return (
    <Text size="sm">
      Глубже искали {formatDate(deeper.at)}
      {deeper.note ? `: ${deeper.note}` : ''}.
    </Text>
  );
}

/** Блок в карточке донора: итог прошлого раза, кнопка и строка задачи. */
export function DeeperBlock({ donor }: { donor: DonorCardWithReach }) {
  const { can } = useSession();
  const { jobId, running, start } = useDeeper(donor.id);
  const deeper = donor.deeper;
  if (deeper === undefined) return null;
  const may = deeper.refusal === null && !running && can('run');
  if (deeper.at === null && !may && jobId === null) return null;

  return (
    <Stack gap="xs" align="flex-start" className="deeperBlock">
      <DeeperPast deeper={deeper} />
      {may && <DeeperStart pending={start.isPending} onStart={() => start.mutate()} />}
      {jobId !== null && <JobLine jobId={jobId} describe={describe} />}
    </Stack>
  );
}

/** Что делает кнопка — и сама кнопка. */
function DeeperStart({ pending, onStart }: { pending: boolean; onStart: () => void }) {
  return (
    <>
      <Text size="sm">{DEEPER_HINT}</Text>
      <Button variant="default" className="press" loading={pending} onClick={onStart}>
        Искать глубже
      </Button>
    </>
  );
}

/** Значок в строке списка доноров, рядом с «адреса нет»: только там, где сервер
 *  разрешает. Значок, а не слово: колонка «Адреса» мерена по самому длинному значку
 *  (`DonorsTable`), и кнопка словом увела бы её за край. Имя и подсказка — словами.
 *  Щелчок по нему не открывает карточку — строка щёлкается целиком. */
export function DeeperRowIcon({ donor }: { donor: DonorRowWithReach }) {
  const { can } = useSession();
  const { running, start } = useDeeper(donor.id);
  if (!can('run') || (!donor.deeper && !running)) return null;
  if (running) return <Loader size={14} aria-label={`Ищем адрес глубже: ${donor.host}`} />;
  return (
    <Tooltip label="Искать адрес глубже" withArrow openDelay={300}>
      <ActionIcon
        variant="subtle"
        color="gray"
        size="sm"
        radius="xl"
        className="press"
        aria-label={`Искать адрес глубже: ${donor.host}`}
        loading={start.isPending}
        onClick={(event) => {
          event.stopPropagation();
          start.mutate();
        }}
      >
        <IconSearch size={14} />
      </ActionIcon>
    </Tooltip>
  );
}
