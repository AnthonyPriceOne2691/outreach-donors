/**
 * Слова «Советов судьи»: полосы, число при слове и итог очереди одной строкой. Своим файлом: их
 * читают и строка над очередью (`JudgeApply`), и окно применения (`JudgeApplyModal`), и список
 * партий (`JudgeBatches`).
 */

import type { JudgeBand, JudgeStanding } from '../api/advertisersJudgeApply';
import { formatNumber } from '../format';

/** Полоса словами — на переключателе окна, на кнопке применения и в списке партий. */
export const BAND_WORDS: Record<JudgeBand, string> = { skip: 'не пишем', write: 'пишем' };

/** Неразрывный пробел — кодом знака, а не невидимым знаком в исходнике. */
const NBSP = String.fromCharCode(0xa0);

/** Число держится за слово неразрывным пробелом: «не пишем 120» не рвётся по строкам
 *  (`docs/UI_RULES.md`, 10.11.2026). */
export function counted(words: string, count: number): string {
  return `${words} ${formatNumber(count)}`.replaceAll(' ', NBSP);
}

/** «не пишем 120 · пишем 80 · не понять 30 · без совета 20»; «ждут DR» и «совет устарел» —
 *  только не нулевые: каждая ждущая пара — ровно в одном числе. */
export function standingWords(standing: JudgeStanding): string {
  const parts = [
    counted('не пишем', standing.skip),
    counted('пишем', standing.write),
    counted('не понять', standing.unclear),
    counted('без совета', standing.without),
  ];
  if (standing.waiting_dr > 0) parts.push(counted('ждут DR', standing.waiting_dr));
  if (standing.stale > 0) parts.push(counted('совет устарел', standing.stale));
  return parts.join(' · ');
}

/** Полоса партии словами; незнакомая (сервер новее экрана) — как пришла. */
export function bandWords(band: string): string {
  const known: Partial<Record<string, string>> = BAND_WORDS;
  return known[band] ?? band;
}
