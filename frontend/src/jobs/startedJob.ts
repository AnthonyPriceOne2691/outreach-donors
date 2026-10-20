/**
 * Задача, поставленная кнопкой экрана: постановка, номер и исход.
 *
 * Один порядок у «Найти адрес» и «Искать глубже» в карточке донора и у «Собрать текст» в
 * окне формы (20.10.2026): у каждого экрана была своя копия — номер задачи, запрос её
 * исхода, «поставлено» и «не поставили» уведомлением, конец задачи один раз. Исход
 * читается тем же запросом и кэшем, что у строки задачи (`JobLine`): второго опроса нет.
 *
 * **Номер переживает уход с экрана**, если дан ключ (`remember`): задача идёт минутами, и
 * карточка, открытая заново, ведёт ту же задачу, а не теряет её.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { refusalOf } from '../api/client';
import { fetchJob } from '../api/jobs';
import type { JobState } from '../api/types';
import { notify } from '../notices';
import { remember as keep, remembered } from '../storage';
import { jobRestarted } from './JobLine';

/** Задача ещё не кончилась: стоит в очереди, идёт или ждёт повтора. */
export const ACTIVE_JOB: ReadonlySet<JobState> = new Set(['queued', 'running', 'retry_wait']);

/** Исход задачи по номеру. `poll` — спрашивать самим, пока она идёт: там, где строки
 *  задачи нет и опросить её некому (значок в строке списка). */
export function useJob(jobId: string | null, poll = false) {
  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => fetchJob(jobId ?? ''),
    enabled: jobId !== null,
    retry: false,
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return poll && (state === undefined || ACTIVE_JOB.has(state)) ? 3_000 : false;
    },
  });
  const state = job.data?.state;
  return { state, running: state !== undefined && ACTIVE_JOB.has(state) };
}

export interface StartedJobOptions<Answer> {
  /** Ключ, под которым номер помнится между заходами; без него — до ухода с экрана. */
  remember?: string;
  /** «Поставлено» уведомлением; без него молча — исход и так скажет строка задачи. */
  queued?: string;
  /** Заголовок уведомления об отказе постановки; текст — словами сервера. */
  refused: string;
  /** Номер у задачи постоянный: прежний итог под ним перечитать (`jobRestarted`). */
  restart?: boolean;
  /** Сервер ответил готовым, без задачи (собранный раньше текст). */
  onAnswer?: (answer: Answer) => void;
  poll?: boolean;
}

/** Поставить задачу кнопкой и следить за ней. `start` — запрос постановки. */
export function useStartedJob<Answer extends { job_id: string | null }, Vars = void>(
  start: (vars: Vars) => Promise<Answer>,
  options: StartedJobOptions<Answer>,
) {
  const queryClient = useQueryClient();
  const key = options.remember;
  const [jobId, setJobId] = useState<string | null>(() => (key ? remembered(key) : null));
  const { state, running } = useJob(jobId, options.poll);
  const mutation = useMutation({
    mutationFn: start,
    onSuccess: async (answer) => {
      if (answer.job_id === null) {
        options.onAnswer?.(answer);
        return;
      }
      if (key) keep(key, answer.job_id);
      setJobId(answer.job_id);
      if (options.restart) await jobRestarted(queryClient, answer.job_id);
      if (options.queued) notify({ message: options.queued, color: 'green' });
    },
    onError: (failure) =>
      notify({ title: options.refused, message: refusalOf(failure), color: 'red' }),
  });
  return { jobId, state, running, start: mutation };
}

/** Конец задачи — один раз на задачу: перечитать то, что она поменяла. Задача под тем же
 *  номером снова идёт — это следующая, и о её конце тоже скажут. */
export function useJobEnd(
  jobId: string | null,
  state: JobState | undefined,
  onEnd: (state: JobState) => void,
) {
  const told = useRef<string | null>(null);
  useEffect(() => {
    if (jobId === null || state === undefined) return;
    if (ACTIVE_JOB.has(state)) {
      told.current = null;
      return;
    }
    if (told.current === jobId) return;
    told.current = jobId;
    onEnd(state);
  }, [jobId, state, onEnd]);
}
