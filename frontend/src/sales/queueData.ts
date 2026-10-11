/**
 * Запросы вкладки «Очередь писем»: очередь гипотезы, сборка задачей, итог сборки словами.
 *
 * Ключ кэша — гипотеза: смена гипотезы не показывает чужие числа, пока едут свои.
 * Сборка идёт задачей (модель на каждое письмо — минуты): номер задачи экран помнит
 * по гипотезе и переживает перезагрузку, а когда задача кончилась, перечитывает очередь.
 *
 * **Отличие каждого письма — из итога сборки** (`letters` отчёта): лид, отличие словами
 * сервера, принятые зоны (остальные в этом письме — текстом шаблона), попыток, в очереди или
 * ждёт. Число — сервера (`difference_text`): правило числа у края коридора одно, экран его не
 * пересчитывает.
 */

import { useQuery } from '@tanstack/react-query';

import { readSalesQueue } from '../api/sales';
import type { SalesQueueLetter } from '../api/salesTypes';
import { formatNumber, plural } from '../format';

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

/** Письма сборки из её отчёта: ждущие первыми — им нужен взгляд, — внутри по порядку
 *  сборки. Итог сборки прежней версии сервера писем по одному не знает — списка нет. */
export function buildLetters(report: Record<string, unknown> | null): SalesQueueLetter[] {
  const letters = report?.letters;
  if (!Array.isArray(letters)) return [];
  return [...(letters as SalesQueueLetter[])].sort((a, b) => Number(a.queued) - Number(b.queued));
}

/** Письмо одной строкой: лид (имя или номер), отличие, принятые зоны, попыток, в очереди
 *  или ждёт. Зон в итоге прежней версии сервера нет — о них строка молчит. */
export function letterLine(letter: SalesQueueLetter): string {
  const who = letter.name ?? `лид №${letter.lead_id}`;
  const zones = Array.isArray(letter.zones)
    ? ` (${letter.zones.length > 0 ? `переписаны ${letter.zones.join(', ')}` : 'переписанных зон нет'})`
    : '';
  const tries = `${formatNumber(letter.attempts)} ${plural(letter.attempts, 'попытка', 'попытки', 'попыток')}`;
  const state = letter.queued ? 'в очереди' : 'ждёт';
  return `${who} — отличие ${letter.difference_text}${zones}, ${tries} — ${state}`;
}
