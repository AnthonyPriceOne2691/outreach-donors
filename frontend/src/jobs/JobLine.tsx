/**
 * Строка исхода фоновой задачи: идёт, ждёт повтора, готово, не выполнена, упала.
 *
 * До неё экран, поставивший задачу, узнавал об исходе только по тому,
 * появился ли результат: упавшая сборка писем выглядела как «письма просто
 * не появились», и человек гадал. Теперь исход называется словами, с причиной
 * и временем следующей попытки.
 */
import { Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import type { QueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { fetchJob } from '../api/jobs';
import type { JobCard, JobState } from '../api/types';
import { formatDateTime } from '../format';

/** Пока задача не кончилась, экран спрашивает о ней сам. */
const ACTIVE: ReadonlySet<JobState> = new Set(['queued', 'running', 'retry_wait']);

const jobKey = (jobId: string) => ['job', jobId] as const;

/** Под этим номером поставлена новая задача. Номер бывает постоянным — сборка и пачка
 *  писем идут по одной на этап и аудиторию (проверка QA 10.10.2026), — и строка с ним
 *  уже знает исход прежней задачи и сама больше не спрашивает: без этого она так и
 *  показывала бы прежний итог. */
export function jobRestarted(client: QueryClient, jobId: string): Promise<void> {
  return client.invalidateQueries({ queryKey: jobKey(jobId) });
}

const COLORS: Record<JobState, string> = {
  queued: 'dimmed',
  running: 'dimmed',
  retry_wait: 'yellow',
  done: 'green',
  refused: 'red',
  failed: 'red',
  unknown: 'yellow',
};

export function jobSentence(job: JobCard): string {
  const parts = [`${job.kind.charAt(0).toUpperCase()}${job.kind.slice(1)}: ${job.title}`];
  if (job.error) parts.push(job.error);
  if (job.state === 'retry_wait' && job.next_try_at) {
    parts.push(`следующая попытка ${formatDateTime(job.next_try_at)}`);
  }
  if (job.state === 'retry_wait' && job.retries_left !== null) {
    parts.push(`осталось попыток: ${job.retries_left}`);
  }
  return parts.join(' — ');
}

export function JobOutcome({ job }: { job: JobCard }) {
  return (
    <Text size="sm" c={COLORS[job.state]} role="status">
      {jobSentence(job)}
    </Text>
  );
}

/** Следит за задачей по номеру и говорит, когда она кончилась.
 *
 *  `describe` — итог законченной задачи словами из её отчёта: «ушло 12,
 *  стоп-лист — 1, осталось 3». Без него строка говорит только исход. */
export function JobLine({
  jobId,
  onFinished,
  describe,
}: {
  jobId: string;
  onFinished?: () => void;
  describe?: (report: Record<string, unknown>) => string | null;
}) {
  const { data } = useQuery({
    queryKey: jobKey(jobId),
    queryFn: () => fetchJob(jobId),
    refetchInterval: (query) =>
      query.state.data === undefined || ACTIVE.has(query.state.data.state) ? 3_000 : false,
    retry: false,
  });
  const finished = data !== undefined && !ACTIVE.has(data.state);
  // Один раз на задачу: колбэк экрана меняется на каждой перерисовке,
  // и без этого список перезапрашивался бы по кругу.
  const told = useRef<string | null>(null);

  useEffect(() => {
    // Задача под тем же номером снова идёт — это следующая (`jobRestarted`): о её конце
    // тоже надо сказать.
    if (!finished) {
      told.current = null;
      return;
    }
    if (told.current !== jobId) {
      told.current = jobId;
      onFinished?.();
    }
  }, [finished, jobId, onFinished]);

  if (!data) return null;
  const said = data.state === 'done' && data.report && describe ? describe(data.report) : null;
  return (
    <>
      <JobOutcome job={data} />
      {said ? <Text size="sm">{said}</Text> : null}
    </>
  );
}
