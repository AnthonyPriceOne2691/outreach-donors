/**
 * Запросы вкладки «Очередь писем»: очередь гипотезы, сборка задачей, итог сборки словами.
 *
 * Ключ кэша — гипотеза: смена гипотезы не показывает чужие числа, пока едут свои.
 * Сборка идёт задачей (модель на каждое письмо — минуты): номер задачи экран помнит
 * по гипотезе и переживает перезагрузку, а когда задача кончилась, перечитывает очередь.
 *
 * **Отличие каждого письма — из итога сборки** (`letters` отчёта): лид, отличие словами
 * сервера, принятые зоны (остальные в этом письме — текстом шаблона), попыток, в очереди или
 * ждёт; добивки с зонами rewrite — отличие или «шаблоном». Число — сервера
 * (`difference_text`): правило числа у края коридора одно, экран его не пересчитывает.
 *
 * **Тексты добивок письма — с сервера** (`useLeadFollowups`): какими их отправит проход
 * добивок, тем же правилом, что у отправки, — а не из итога сборки: цепочку могли править
 * после неё.
 */

import { useQuery } from '@tanstack/react-query';

import { readLeadFollowups, readSalesQueue } from '../api/sales';
import { FOLLOWUP_ORDINALS } from '../api/salesLabels';
import type { SalesQueueFollowup, SalesQueueLetter } from '../api/salesTypes';
import { formatNumber, plural } from '../format';

export const QUEUE_QUERY_KEY = ['sales', 'queue'] as const;

export function useSalesQueue(hypothesis: number) {
  return useQuery({
    queryKey: [...QUEUE_QUERY_KEY, hypothesis],
    queryFn: () => readSalesQueue(hypothesis),
  });
}

/** Добивки лида, которые ещё уйдут, — по раскрытию строки письма сборки. */
export function useLeadFollowups(lead: number) {
  return useQuery({
    queryKey: ['sales', 'lead-followups', lead],
    queryFn: () => readLeadFollowups(lead),
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
  const template = Number(report.followups_template ?? 0);
  if (template > 0) parts.push(`Добивок шаблоном: ${formatNumber(template)}.`);
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

/** Добивки письма с зонами rewrite: те, что собраны с ним. В итоге прежней версии сервера их
 *  нет — пусто. */
export function followupsOf(letter: SalesQueueLetter): SalesQueueFollowup[] {
  return Array.isArray(letter.followups) ? letter.followups : [];
}

/** Добивки письма одной частью строки: «добивки: первая — отличие 19%, вторая — шаблоном».
 *  Почему шаблоном — с числами каждой попытки — в раскрытии, словами сервера. */
function followupsText(letter: SalesQueueLetter): string {
  const parts = followupsOf(letter).map((later) => {
    const which = FOLLOWUP_ORDINALS[later.step] ?? `шаг ${later.step}`;
    return later.difference_text === null
      ? `${which} — шаблоном`
      : `${which} — отличие ${later.difference_text}`;
  });
  return parts.length > 0 ? `; добивки: ${parts.join(', ')}` : '';
}

/** Письмо одной строкой: лид (имя или номер) и — в очереди — отличие, принятые зоны,
 *  попыток и добивки; ждёт — почему, словами сервера по этому письму (вне коридора — с
 *  отличием каждой попытки). Зон, причины и добивок в итоге прежней версии сервера нет — о
 *  них строка молчит. */
export function letterLine(letter: SalesQueueLetter): string {
  const who = letter.name ?? `лид №${letter.lead_id}`;
  if (typeof letter.reason === 'string') return `${who} — ${letter.reason}`;
  const zones = Array.isArray(letter.zones)
    ? ` (${letter.zones.length > 0 ? `переписаны ${letter.zones.join(', ')}` : 'переписанных зон нет'})`
    : '';
  const tries = `${formatNumber(letter.attempts)} ${plural(letter.attempts, 'попытка', 'попытки', 'попыток')}`;
  const state = letter.queued ? 'в очереди' : 'ждёт';
  return `${who} — отличие ${letter.difference_text}${zones}, ${tries} — ${state}${followupsText(letter)}`;
}
