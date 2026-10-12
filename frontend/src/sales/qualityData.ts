/**
 * Вкладка «Агент: качество»: запрос, основы долей и доли словами.
 *
 * **Экран не считает сам — только делит.** Числа, итог ворот автоответа и его слова — сервера
 * (`GET /sales/agent-quality`). Доля — от решений оператора: «как есть», «с правкой» и
 * «отклонено» — от решений по готовым черновикам, верное и ложное молчание — от молчаний
 * агента с исходом. Ждущие и отданные человеку в основу не входят.
 *
 * **Малая основа — «данных мало», без процента.** Доля показывается, только если основа не
 * меньше N (`limits.min_n`, настройка сервера): процент из горсти решений читался бы выводом.
 *
 * **Доля у порога — по ту же сторону порога, что и сама** (`docs/UI_RULES.md`, «Число рядом
 * с границей»): 3 999 из 5 000 при пороге 80% — «79,98%», а не «80%».
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { readAgentQuality } from '../api/salesQuality';
import type { CohortView, QualityCounts, QualityLimits } from '../api/salesQuality';
import { formatPercent } from '../format';

export const QUALITY_QUERY_KEY = ['sales', 'agent-quality'] as const;

/** Номер когорты в адресе — как его пишет сервер: десять знаков отпечатка. */
export const COHORT_ID = /^[0-9a-f]{10}$/;

/** Качество агента по когорте; пусто — текущая. Прежний ответ стоит, пока едет новый. */
export function useAgentQuality(cohort: string) {
  return useQuery({
    queryKey: [...QUALITY_QUERY_KEY, cohort],
    queryFn: () => readAgentQuality(cohort),
    placeholderData: keepPreviousData,
  });
}

/** Решений оператора по готовым черновикам — основа «как есть», «с правкой», «отклонено». */
export function decisionsOf(counts: QualityCounts): number {
  return counts.as_is + counts.edited + counts.rejected;
}

/** Молчаний агента с исходом — основа верного и ложного молчания. */
export function silencesOf(counts: QualityCounts): number {
  return counts.true_silence + counts.false_silence;
}

/** Отдано человеку — всего, как бы он ни решил. */
export function heldOf(counts: QualityCounts): number {
  return counts.held_sent + counts.held_rejected + counts.held_waiting;
}

/** Сколько знаков после запятой бывает у доли: дальше — ничего не различить. */
const MAX_DIGITS = 4;

/** Доля процентами: десятая — когда она есть; у порога `floor` (процентами) точность растёт,
 *  пока показанное число не встанет по ту же сторону порога, что и само. */
export function percentOf(part: number, whole: number, floor?: number): string {
  const exact = (part * 100) / whole;
  if (floor === undefined) return formatPercent(part / whole);
  const above = part * 100 >= floor * whole;
  let digits = 1;
  while (digits < MAX_DIGITS) {
    const scale = 10 ** digits;
    const shown = Math.round(exact * scale) / scale;
    if (shown >= floor === above) break;
    digits += 1;
  }
  return formatPercent(part / whole, digits);
}

/** Хватает ли основы, чтобы назвать долю. */
export function enough(whole: number, limits: QualityLimits): boolean {
  return whole > 0 && whole >= limits.min_n;
}

/** Доля словами — или «данных мало», если основа меньше N. */
export function shareText(
  part: number,
  whole: number,
  limits: QualityLimits,
  floor?: number,
): string {
  return enough(whole, limits) ? percentOf(part, whole, floor) : 'данных мало';
}

/** Версия агента одной строкой: промпт черновика и база знаний — то, что меняют чаще. */
export function versionName(cohort: CohortView): string {
  return `${cohort.reply ?? 'промпт —'} · база ${cohort.kb ?? '—'}`;
}

/** Все версии, по которым написан черновик: промпты, таблица ходов, база знаний. */
export function versionDetail(cohort: CohortView): string {
  const known = (value: string | null) => value ?? '—';
  return (
    `черновик ${known(cohort.reply)} · ситуация ${known(cohort.situation)} · ` +
    `судья ${known(cohort.judge)} · ходы ${known(cohort.moves)} · база знаний ${known(cohort.kb)}`
  );
}
