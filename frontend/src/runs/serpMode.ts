/**
 * Режим покупки выдачи у DataForSEO — цена против ожидания.
 *
 * Слово владельца 14.10.2026: «в первую очередь скорость, по умолчанию быстрый»;
 * медленный и дешёвый — на выбор. Сервер знает три режима
 * (`backend/features/serp/modes.py`), экран предлагает два крайних: средний
 * (приоритетная очередь) ничего не добавляет к выбору «быстро или дёшево», а лишняя
 * строка в списке — лишний вопрос. Цена и ожидание — в смете, до траты.
 */

import type { SerpMode } from '../api/types';

/** Что предлагает список. Первым — умолчание. */
export const SERP_MODES = ['live', 'standard'] as const satisfies readonly SerpMode[];

export type OfferedSerpMode = (typeof SERP_MODES)[number];

export const DEFAULT_SERP_MODE: OfferedSerpMode = 'live';

export const SERP_MODE_TITLES: Record<SerpMode, string> = {
  live: 'Быстро — секунды',
  priority: 'Средне — около минуты',
  standard: 'Дешевле — около 5 минут',
};

export function isSerpMode(value: unknown): value is OfferedSerpMode {
  return SERP_MODES.some((mode) => mode === value);
}

/** «за секунды», «около минуты», «около 5 минут» — ожидание выдачи словами. */
export function waitTitle(seconds: number): string {
  if (seconds < 60) return 'за секунды';
  const minutes = Math.round(seconds / 60);
  return minutes === 1 ? 'около минуты' : `около ${minutes} минут`;
}
