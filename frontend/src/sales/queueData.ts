/**
 * Запросы вкладки «Очередь писем»: очередь гипотезы, сборка задачей, итог сборки словами.
 *
 * Ключ кэша — гипотеза: смена гипотезы не показывает чужие числа, пока едут свои.
 * Сборка идёт задачей (модель на каждое письмо — минуты): номер задачи экран помнит
 * по гипотезе и переживает перезагрузку, а когда задача кончилась, перечитывает очередь.
 */

import { useQuery } from '@tanstack/react-query';

import { readSalesQueue } from '../api/sales';
import { formatNumber } from '../format';

export const QUEUE_QUERY_KEY = ['sales', 'queue'] as const;

export function useSalesQueue(hypothesis: number) {
  return useQuery({
    queryKey: [...QUEUE_QUERY_KEY, hypothesis],
    queryFn: () => readSalesQueue(hypothesis),
  });
}

/** Где экран помнит номер последней сборки гипотезы. */
export function buildJobKey(hypothesis: number): string {
  return `sales:last-queue-build:${hypothesis}`;
}

/** Итог сборки одной строкой — из отчёта задачи (`QueueReport` сервера): сколько
 *  писем собрано, сколько лидов ждёт и почему, почему сборка остановилась. */
export function queueLine(report: Record<string, unknown>): string {
  const waiting = (report.waiting ?? {}) as Record<string, number>;
  const parts = [
    `Новых писем ${formatNumber(Number(report.prepared ?? 0))}, ` +
      `собрано заново ${formatNumber(Number(report.refreshed ?? 0))}.`,
  ];
  const reasons = Object.entries(waiting).map(([why, count]) => `${why}: ${formatNumber(count)}`);
  if (reasons.length > 0) parts.push(`Ждут — ${reasons.join('; ')}.`);
  if (typeof report.stopped === 'string') parts.push(`Остановлено: ${report.stopped}`);
  return parts.join(' ');
}
