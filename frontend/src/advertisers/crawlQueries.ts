/**
 * Запросы обхода доноров, общие у вкладки «Обход доноров» (`CrawlTab`) и сводки «Проверки»
 * (`CrawlSummary`): ключи, сводка с опросом, пока что-то идёт, и запуск обходов.
 *
 * **Опрос — раз в десять секунд и только пока обход идёт или ждёт.** Спрашивается лёгкая сводка
 * (`/crawls/summary`), а не страница доноров: страница перечитывается, когда сводка сменилась.
 * Кончился обход — кандидаты по нему считаются сами, и очередь рекламодателей перечитывается, а
 * не ждёт перезагрузки страницы.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import { refusalOf } from '../api/client';
import type { CrawlBulkResult, CrawlDonorQuery, CrawlLaunch } from '../api/crawls';
import { fetchCrawlSummary, startCrawls, startCrawlsByFilter } from '../api/crawls';
import { formatNumber, plural } from '../format';
import { notify } from '../notices';
import { BACKLOG_KEY } from './CrawlBacklog';

export const CRAWL_SUMMARY_KEY = ['crawl-summary'] as const;
export const CRAWL_DONORS_KEY = ['crawl-donors'] as const;
export const CRAWLS_KEY = ['crawls'] as const;

/** Пока обход идёт, сервер спрашивается раз в десять секунд: страница донора — около
 *  полутора секунд, за десять их набегает пачка. */
export const POLL_MS = 10_000;

/** Что вышло из нажатия — одной строкой, с причинами по каждому непоставленному. */
export function launchSummary(done: CrawlLaunch): string {
  const parts = [`Поставлено: ${formatNumber(Object.keys(done.queued).length)}.`];
  if (done.busy.length > 0) parts.push(`Уже идут: ${done.busy.join(', ')}.`);
  for (const [host, why] of Object.entries(done.refused)) parts.push(`${host}: ${why}.`);
  if (done.failed.length > 0) {
    parts.push(`Очередь не ответила: ${done.failed.join(', ')} — попробуйте ещё раз.`);
  }
  return parts.join(' ');
}

function donorsWord(count: number): string {
  return `${formatNumber(count)} ${plural(count, 'донор', 'донора', 'доноров')}`;
}

/** Что вышло — одной строкой уведомления. */
export function bulkSummary(done: CrawlBulkResult): string {
  if (done.asked === 0)
    return 'Под фильтром обойти некого: обходы уже идут или ждут, доноры в очереди загрузки или цены нет.';
  if (done.deferred) {
    return (
      `В очередь загрузки — ${donorsWord(done.backlog ?? done.asked)}: обходчикам их отдают по ` +
      'мере, как освобождаются, а обход, поставленный кнопкой, идёт первым.'
    );
  }
  return queuedSummary(done.asked, done.queued ?? 0, done.busy ?? 0, done.failed ?? 0);
}

/** Поставлены сразу: сколько из скольких, сколько уже шли и скольких очередь не приняла. */
function queuedSummary(asked: number, queued: number, busy: number, failed: number): string {
  const parts = [`Поставлено: ${formatNumber(queued)} из ${formatNumber(asked)}.`];
  if (busy > 0) parts.push(`Уже шли: ${formatNumber(busy)}.`);
  if (failed > 0) parts.push(`Очередь не ответила: ${formatNumber(failed)} — попробуйте ещё раз.`);
  return parts.join(' ');
}

/** Перечитать всё, что показывает обход: сводку, доноров, журнал, очередь загрузки. */
export function refreshCrawls(queryClient: ReturnType<typeof useQueryClient>): Promise<unknown> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: CRAWL_SUMMARY_KEY }),
    queryClient.invalidateQueries({ queryKey: CRAWL_DONORS_KEY }),
    queryClient.invalidateQueries({ queryKey: CRAWLS_KEY }),
    queryClient.invalidateQueries({ queryKey: BACKLOG_KEY }),
  ]);
}

/** Сводка обхода с опросом, пока что-то идёт или ждёт. Сменилась на ходу — перечитываются
 *  доноры и журнал (там ход «340 из 1000»); идущих стало меньше — ещё и очередь рекламодателей. */
export function useCrawlSummary() {
  const queryClient = useQueryClient();
  const summary = useQuery({
    queryKey: CRAWL_SUMMARY_KEY,
    queryFn: fetchCrawlSummary,
    refetchInterval: (query) => ((query.state.data?.active ?? 0) > 0 ? POLL_MS : false),
  });
  const active = summary.data?.active ?? 0;
  const stamp = summary.dataUpdatedAt;
  const before = useRef(active);
  useEffect(() => {
    if (active > 0 || active < before.current) {
      void queryClient.invalidateQueries({ queryKey: CRAWL_DONORS_KEY });
      void queryClient.invalidateQueries({ queryKey: CRAWLS_KEY });
    }
    if (active < before.current) void queryClient.invalidateQueries({ queryKey: ['advertisers'] });
    before.current = active;
  }, [active, stamp, queryClient]);
  return summary;
}

/** Запуск обходов названных доноров; исход — словами уведомления. */
export function useStartCrawls(onQueued?: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (hosts: string[]) => startCrawls(hosts),
    onSuccess: async (done) => {
      onQueued?.();
      await refreshCrawls(queryClient);
      const clean = done.failed.length === 0 && Object.keys(done.refused).length === 0;
      notify({ message: launchSummary(done), color: clean ? 'green' : 'yellow' });
    },
    onError: (failure) =>
      notify({ title: 'Не поставили', message: refusalOf(failure), color: 'red' }),
  });
}

/** «Обойти всех по фильтру»: условия вкладки и сколько человек видел на кнопке; исход — словами
 *  уведомления (`bulkSummary`). */
export function useStartByFilter(onDone: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ query, expected }: { query: CrawlDonorQuery; expected: number }) =>
      startCrawlsByFilter(query, expected),
    onSuccess: async (done) => {
      onDone();
      await refreshCrawls(queryClient);
      notify({ message: bulkSummary(done), color: done.asked > 0 ? 'green' : 'yellow' });
    },
    onError: (failure) =>
      notify({ title: 'Не поставили', message: refusalOf(failure), color: 'red' }),
  });
}
