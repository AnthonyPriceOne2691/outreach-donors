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
