/**
 * Слова списка сайтов прогона и пересуда: полосы, вкладки, кто решил, «было → стало».
 *
 * Одно место на таблицу, вкладки и итог пересуда: полоса в строке, во вкладке и в «было → стало»
 * называется одним словом (`docs/UI_RULES.md`, «Одно слово — один смысл на всех экранах»).
 */

import type { SegmentLane } from './segmentApi';
import type { RejudgeMove, SiteDecider, SiteTab } from './SegmentSitesApi';

/** Полоса сайта словами — в строке и в «было → стало». */
export const LANE_WORDS: Record<SegmentLane, string> = {
  pending: 'ещё не судили',
  accept: 'принят',
  review: 'на проверке',
  reject: 'отсечён',
};

/** Цвет полосы — по смыслу: мята — принят, янтарь — решает человек, роза — отсечён. */
export const LANE_COLORS: Record<SegmentLane, string> = {
  pending: 'gray',
  accept: 'green',
  review: 'yellow',
  reject: 'red',
};

/** Вкладки списка — в порядке работы: все, кого взяли, кого решать, кого отсекли, кого ждут. */
export const TAB_TITLES: Record<SiteTab, string> = {
  all: 'Все',
  accept: 'Приняты',
  review: 'На проверку',
  reject: 'Отсечены',
  pending: 'Ещё не судили',
  changed: 'Сменили при пересуде',
};

export const TAB_KEYS = Object.keys(TAB_TITLES) as SiteTab[];

/** Кто решил — словами фильтра и строки. */
export const DECIDER_WORDS: Record<SiteDecider, string> = {
  rule: 'правило',
  model: 'модель',
  human: 'человек',
};

export const DECIDER_KEYS = Object.keys(DECIDER_WORDS) as SiteDecider[];

/** «Было → стало» одной строкой: «принят → отсечён». */
export function moveWords(move: Pick<RejudgeMove, 'was' | 'now'>): string {
  return `${LANE_WORDS[move.was]} → ${LANE_WORDS[move.now]}`;
}

/** Почта — до «@»: в строке не шире колонки, целиком — в подсказке. */
export function personOf(email: string): string {
  return email.split('@', 1)[0] ?? email;
}

/** Начало причины у отказа судьи в наблюдении — как его пишет сервер (`segment_judge.SHADOW_CUT`). */
const SHADOW_CUT = 'Отказ: ';
/** Так сервер писал его до 20.10.2026; причины в базе не переписываются. */
const SHADOW_CUT_BEFORE = 'судья в наблюдении отрезал бы: ';

/**
 * Причина словами экрана — одна для старых и новых: длинное начало отказа судьи в наблюдении
 * («судья в наблюдении отрезал бы: …», до 20.10.2026) — коротким «Отказ: …», как его пишет сервер
 * теперь (тест продаж 20.10: «для экономии места»). Остальное — как есть.
 */
export function reasonWords(reason: string): string {
  return reason.startsWith(SHADOW_CUT_BEFORE)
    ? `${SHADOW_CUT}${reason.slice(SHADOW_CUT_BEFORE.length)}`
    : reason;
}

/** Под полосой сайта «на проверке», которому судья отказал: отказ — мнение, решение за человеком. */
export const HUMAN_DECIDES = 'решает человек';
