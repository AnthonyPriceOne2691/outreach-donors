/**
 * Вкладка «Сбор базы»: запросы с кэшем и слова — режим судьи, полосы, глубина, шапка прогона.
 *
 * **Отчёт прогона и список прогонов опрашиваются, пока прогон идёт** — как прогон и история
 * доноров: этапы коммитят своё по ходу, и числа растут на глазах. Идёт ли прогон, решает сервер
 * по очереди задач (`run.active`, `segmentStage.ts`): после «Продолжить» и «Найти адреса
 * принятым» его задача стоит в очереди — экран опрашивает до конца прохода без перезагрузки.
 *
 * **Прогон дошёл до лидов и кончился — перечитываются лиды и гипотезы** (`useRunSettled`):
 * число «Лиды — N» во вкладках и список лидов стояли прежними до перезагрузки.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { QueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import { countryTitle } from '../api/labels';
import { leadReasonTitle } from '../api/salesLabels';
import { formatDateTime, formatNumber, plural } from '../format';
import { HYPOTHESES_QUERY_KEY } from './hypothesisData';
import { readRun, readSegment } from './segmentApi';
import type { SegmentHome, SegmentMode, SegmentRunDetail, SegmentRunRow } from './segmentApi';
import { isActive } from './segmentStage';

export { STAGE_WORDS } from './segmentStage';

export const SEGMENT_QUERY_KEY = ['sales', 'segment'] as const;
/** Лиды раздела — начало ключа списка лидов (`LeadsPane`): сброс по началу ключа. */
const LEADS_QUERY_KEY = ['sales', 'leads'] as const;

/** Отчёт идущего прогона — раз в три секунды; список прогонов — раз в пять, как история
 *  прогонов доноров. */
const RUN_POLL_MS = 3_000;
const LIST_POLL_MS = 5_000;

/** Как часто перечитывать список прогонов: пока есть идущий — раз в пять секунд. */
export function listPoll(home: SegmentHome | undefined): number | false {
  return (home?.runs ?? []).some(isActive) ? LIST_POLL_MS : false;
}

/** Как часто перечитывать отчёт прогона: пока он идёт — раз в три секунды. */
export function runPoll(detail: SegmentRunDetail | undefined): number | false {
  return detail !== undefined && isActive(detail.run) ? RUN_POLL_MS : false;
}

export function useSegment(hypothesis: number) {
  return useQuery({
    queryKey: [...SEGMENT_QUERY_KEY, 'home', hypothesis],
    queryFn: () => readSegment(hypothesis),
    // Пока есть идущий прогон, список обновляется сам: стадия в нём — живая.
    refetchInterval: (query) => listPoll(query.state.data),
  });
}

export function useRun(run: number | null) {
  return useQuery({
    queryKey: [...SEGMENT_QUERY_KEY, 'run', run],
    queryFn: () => readRun(run ?? 0),
    enabled: run !== null,
    refetchInterval: (query) => runPoll(query.state.data),
  });
}

/**
 * Прогон дошёл до лидов или кончился — перечитать гипотезы (число лидов во вкладках), список
 * лидов и список прогонов. Раз на переход: колбэк смотрит на стадию и «идёт» прошлой
 * отрисовки, а не на каждый опрос.
 */
export function useRunSettled(detail: SegmentRunDetail) {
  const client = useQueryClient();
  const { stage } = detail.run;
  const active = isActive(detail.run);
  const before = useRef<{ stage: string; active: boolean } | null>(null);
  useEffect(() => {
    const was = before.current;
    before.current = { stage, active };
    if (was === null) return;
    const reachedLeads = stage === 'leads' && was.stage !== 'leads';
    const ended = was.active && !active;
    if (!reachedLeads && !ended) return;
    rereadLeads(client);
    if (ended) void client.invalidateQueries({ queryKey: [...SEGMENT_QUERY_KEY, 'home'] });
  }, [stage, active, client]);
}

/** Лиды сменились — перечитать гипотезы (число лидов во вкладках) и список лидов: прогон дошёл до
 *  лидов, отмена решения по сайту сняла его лида. */
export function rereadLeads(client: QueryClient): void {
  void client.invalidateQueries({ queryKey: HYPOTHESES_QUERY_KEY });
  void client.invalidateQueries({ queryKey: LEADS_QUERY_KEY });
}

/** Режим судьи словами — так, как он влияет на сайты. */
export const MODE_WORDS: Record<SegmentMode, string> = {
  shadow:
    'судья в наблюдении: его «отказ» не отсекает сайт — сайт в «Судья отказал\u00a0бы», лид из него — только после «Принять» человека',
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

/** Шапка отчёта: язык писем, глубина выдачи, кто и когда запустил, когда кончился проход. */
export function runFacts(run: SegmentRunRow): string {
  const facts = [
    `язык писем — ${LANGUAGE_TITLES[run.language] ?? run.language}`,
    `выдача — ${depthTitle(run.depth_pages)}`,
  ];
  const who = run.created_by === null ? '' : ` ${run.created_by.split('@', 1)[0]}`;
  facts.push(`запустил${who} ${formatDateTime(run.created_at)}`);
  if (run.finished_at !== null && !isActive(run)) {
    facts.push(`кончился ${formatDateTime(run.finished_at)}`);
  }
  const line = facts.join(' · ');
  return `${line.charAt(0).toUpperCase()}${line.slice(1)}.`;
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

/** Итог задачи прогона одной строкой — для строки задачи. Лиды прохода — из его хода
 *  (`progress`): итог лидов в отчёте копится за все проходы прогона. */
export function runJobLine(report: Record<string, unknown>): string | null {
  if (typeof report.error === 'string') return null;
  const progress = report.progress as { stage?: string; done?: number } | undefined;
  if (progress?.stage === 'leads') {
    return `Проход прогона кончился: лидов заведено ${formatNumber(progress.done ?? 0)}.`;
  }
  const leads = report.leads as { created?: number } | undefined;
  if (leads === undefined) return 'Проход прогона кончился — числа в отчёте ниже.';
  return `Проход прогона кончился: лидов из прогона — ${formatNumber(leads.created ?? 0)}.`;
}

/** Сайтов выдачи, которые гипотеза уже собирала прошлыми прогонами (`report.serp.known`). */
export function knownSites(detail: SegmentRunDetail): number {
  const serp = detail.report.serp;
  if (typeof serp !== 'object' || serp === null) return 0;
  const known = (serp as Record<string, unknown>).known;
  return typeof known === 'number' ? known : 0;
}

/** Второй шанс цитате (судья сегмента v5): у скольких сайтов цитата первого ответа модели не
 *  нашлась в тексте сайта и модель спросили второй раз (`retried`) и у скольких из них цитата
 *  нового ответа нашлась (`held`). Вопрос — один на сайт, поэтому числа — сайты. */
export interface QuoteRetries {
  retried: number;
  held: number;
}

function positive(value: unknown): number {
  return typeof value === 'number' && value > 0 ? value : 0;
}

/** Числа второго шанса из итога судьи — `report.judge` прогона или итог пересуда — как их записал
 *  сервер (`quote_retried`, `quote_retry_held`). Второго вопроса не было или итог записан до судьи
 *  v5 (полей нет) — `null`: строки нет. */
export function quoteRetries(tally: unknown): QuoteRetries | null {
  if (typeof tally !== 'object' || tally === null) return null;
  const found = tally as Record<string, unknown>;
  const retried = positive(found.quote_retried);
  return retried > 0 ? { retried, held: positive(found.quote_retry_held) } : null;
}

/** Второй шанс словами — одними у отчёта прогона и у итога пересуда: «цитату переспросили у
 *  модели: 3 сайта, со второго раза нашлась у 2». */
export function quoteRetryWords({ retried, held }: QuoteRetries): string {
  const sites = `${formatNumber(retried)} ${plural(retried, 'сайт', 'сайта', 'сайтов')}`;
  const second = held > 0 ? `нашлась у ${formatNumber(held)}` : 'не нашлась ни у одного';
  return `цитату переспросили у модели: ${sites}, со второго раза ${second}`;
}
