/**
 * Вкладка «Сбор базы»: запросы с кэшем и слова — этапы, режим судьи, полосы, глубина.
 *
 * **Отчёт прогона опрашивается, пока прогон идёт**: этапы коммитят своё по ходу, и числа
 * полос растут на глазах. Кончился или остановлен — опрос стоит: решение человека и
 * продолжение перечитывают отчёт сами.
 */

import { useQuery } from '@tanstack/react-query';

import { countryTitle } from '../api/labels';
import { leadReasonTitle } from '../api/salesLabels';
import { formatDateTime, formatNumber } from '../format';
import { readRun, readSegment } from './segmentApi';
import type { SegmentMode, SegmentRunDetail, SegmentRunRow, SegmentStage } from './segmentApi';

export const SEGMENT_QUERY_KEY = ['sales', 'segment'] as const;

/** Пока прогон в этих этапах, отчёт опрашивается сам. */
const GOING: ReadonlySet<SegmentStage> = new Set(['queued', 'serp', 'judge', 'contacts']);

export function useSegment(hypothesis: number) {
  return useQuery({
    queryKey: [...SEGMENT_QUERY_KEY, 'home', hypothesis],
    queryFn: () => readSegment(hypothesis),
  });
}

export function useRun(run: number | null) {
  return useQuery({
    queryKey: [...SEGMENT_QUERY_KEY, 'run', run],
    queryFn: () => readRun(run ?? 0),
    enabled: run !== null,
    refetchInterval: (query) =>
      query.state.data !== undefined && GOING.has(query.state.data.run.stage) ? 3_000 : false,
  });
}

/** Этап прогона словами: что сделано и что дальше. */
export const STAGE_WORDS: Record<SegmentStage, string> = {
  queued: 'в очереди задач',
  serp: 'выдача куплена — судит судья',
  judge: 'судья прошёл — ищем адреса',
  contacts: 'адреса найдены — заводим лидов',
  done: 'готово',
  stopped: 'остановлен',
};

/** Режим судьи словами — так, как он влияет на сайты. */
export const MODE_WORDS: Record<SegmentMode, string> = {
  shadow: 'судья в наблюдении: его «отказ» не отсекает сайт, а отдаёт человеку',
  enforce: 'судья режет: «отказ» отсекает сайт',
  off: 'судья выключен: каждый сайт решает человек',
};

/** Что сказал судья сам — в строке «на проверку». */
export const VERDICT_WORDS: Record<string, string> = {
  accept: 'подходит',
  review: 'не понять',
  reject: 'не подходит',
  pending: 'ещё не судил',
};

/** Глубина выдачи — страницы по десять результатов. */
export const DEPTHS = [1, 2, 3, 5, 10];

export function depthTitle(pages: number): string {
  return `${formatNumber(pages * 10)} результатов на ключ`;
}

/** Язык писем: пусто — язык рынка выдачи. */
export const LANGUAGE_TITLES: Record<string, string> = { ru: 'русский', en: 'английский' };

/** Прогон одной строкой списка: номер, когда, сколько ключей, рынок. */
export function runTitle(run: SegmentRunRow): string {
  return (
    `№${run.id} · ${formatDateTime(run.created_at)} · ключей ${formatNumber(run.keywords)} · ` +
    `${countryTitle(run.country)}`
  );
}

/** Лиды прогона и что с ними сделала очистка — одной строкой. */
export function leadsLine(detail: SegmentRunDetail): string {
  const { leads, lead_reasons: reasons } = detail;
  const total = Object.values(leads).reduce((sum, count) => sum + count, 0);
  if (total === 0) return 'Лидов из прогона пока нет.';
  const rejected = Object.entries(reasons)
    .sort(([, a], [, b]) => b - a)
    .map(([code, count]) => `${leadReasonTitle(code)} — ${formatNumber(count)}`);
  const why = rejected.length > 0 ? ` (${rejected.join(', ')})` : '';
  return (
    `Лидов заведено ${formatNumber(total)}: готовы к письмам ${formatNumber(leads.ready ?? 0)}, ` +
    `отклонены очисткой ${formatNumber(leads.rejected ?? 0)}${why}, ` +
    `ждут очистки ${formatNumber(leads.new ?? 0)}.`
  );
}

/** Итог задачи прогона одной строкой — для строки задачи. */
export function runJobLine(report: Record<string, unknown>): string | null {
  const leads = report.leads as { created?: number } | undefined;
  if (typeof report.error === 'string') return null;
  if (leads === undefined) return 'Проход прогона кончился — числа в отчёте ниже.';
  return `Проход прогона кончился: лидов заведено ${formatNumber(leads.created ?? 0)}.`;
}
