/**
 * Слова списка сайтов прогона и пересуда: полосы, вкладки, кто решил, «было → стало».
 *
 * Одно место на таблицу, вкладки и итог пересуда: полоса в строке, во вкладке и в «было → стало»
 * называется одним словом (`docs/UI_RULES.md`, «Одно слово — один смысл на всех экранах»).
 */

import type { SegmentLane } from './segmentApi';
import type { RejudgeMove, SiteDecider, SiteTab } from './SegmentSitesApi';

/** Полоса сайта словами — в строке, во вкладке, на плитке и в «было → стало» одним словом. */
export const LANE_WORDS: Record<SegmentLane, string> = {
  pending: 'ещё не судили',
  accept: 'принят',
  review: 'на проверке',
  would_reject: 'судья отказал бы',
  reject: 'отсечён',
};

/** Цвет полосы — по смыслу: мята — принят, янтарь — решает человек, оранжевый — судья отказал бы
 *  (мнение, сайт не отсечён), роза — отсечён. */
export const LANE_COLORS: Record<SegmentLane, string> = {
  pending: 'gray',
  accept: 'green',
  review: 'yellow',
  would_reject: 'orange',
  reject: 'red',
};

/** Полосы, где решение за человеком («Принять», «Отказ»), — как у сервера
 *  (`segment_models.AWAITS_HUMAN`): «на проверке» решить нужно, «судья отказал бы» — можно. */
export const AWAITS_HUMAN: ReadonlySet<SegmentLane> = new Set<SegmentLane>([
  'review',
  'would_reject',
]);

/** Вкладки списка — в порядке работы: все, кого взяли, кого решать, кому судья отказал бы, кого
 *  отсекли, кого ждут. */
export const TAB_TITLES: Record<SiteTab, string> = {
  all: 'Все',
  accept: 'Приняты',
  review: 'На проверку',
  would_reject: 'Судья отказал бы',
  reject: 'Отсечены',
  pending: 'Ещё не судили',
  changed: 'Сменили при пересуде',
};

export const TAB_KEYS = Object.keys(TAB_TITLES) as SiteTab[];

/** Над таблицей «Судья отказал бы»: что это за сайты и что с ними делать. */
export const WOULD_REJECT_NOTE =
  'Этим сайтам судья отказал бы, но в наблюдении не отсекает их. Решать по ним не обязательно: ' +
  'лид будет только после «Принять».';

/** Плитка отчёта с отказами судьи в наблюдении: «бы» — неразрывно с глаголом, а не одно на второй
 *  строке узкой плитки (замер стенда 20.10: «Судья отказал / бы» на 1440 и на телефоне). */
export const REFUSED_TILE = 'Судья отказал\u00a0бы';

/** Кто решил — словами фильтра и строки. «Язык» — сверка языка сайта с языком писем: кодом, по
 *  буквам видимого текста, без модели. */
export const DECIDER_WORDS: Record<SiteDecider, string> = {
  rule: 'правило',
  model: 'модель',
  language: 'язык',
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

/** Начало причины у отказа судьи в наблюдении, пока такой сайт лежал «на проверке» (20.10.2026).
 *  С варианта (b) сервер его не пишет: что это отказ, говорит полоса «судья отказал бы». */
const SHADOW_CUT = 'Отказ: ';
/** Так сервер писал его до 20.10.2026; причины в базе не переписываются. */
const SHADOW_CUT_BEFORE = 'судья в наблюдении отрезал бы: ';

/**
 * Причина словами экрана — одна для старых и новых: длинное начало отказа судьи в наблюдении
 * («судья в наблюдении отрезал бы: …», до 20.10.2026) — коротким «Отказ: …», как его писал сервер
 * до своей полосы (тест продаж 20.10: «для экономии места»). Остальное — как есть.
 */
export function reasonWords(reason: string): string {
  return reason.startsWith(SHADOW_CUT_BEFORE)
    ? `${SHADOW_CUT}${reason.slice(SHADOW_CUT_BEFORE.length)}`
    : reason;
}

/** Под полосой сайта «на проверке», которому судья отказал (прогоны до своей полосы отказов):
 *  отказ — мнение, решение за человеком. */
export const HUMAN_DECIDES = 'решает человек';
