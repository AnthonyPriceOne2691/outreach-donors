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
import { useEffect, useRef } from 'react';
import { fetchJob } from '../api/jobs';
import type { JobCard, JobState } from '../api/types';
import { formatDateTime } from '../format';

/** Пока задача не кончилась, экран спрашивает о ней сам. */
const ACTIVE: ReadonlySet<JobState> = new Set(['queued', 'running', 'retry_wait']);

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

/** Следит за задачей по номеру и говорит, когда она кончилась. */
export function JobLine({ jobId, onFinished }: { jobId: string; onFinished?: () => void }) {
  const { data } = useQuery({
    queryKey: ['job', jobId],
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
    if (finished && told.current !== jobId) {
      told.current = jobId;
      onFinished?.();
    }
  }, [finished, jobId, onFinished]);

  return data ? <JobOutcome job={data} /> : null;
}
