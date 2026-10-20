/**
 * Срок повтора словами — часами человека: сегодня — время, другой день — дата и время. Одно
 * правило у отчёта прогона (`SegmentWaits`) и у итога очистки (`cleanData`): у срока «ещё
 * проверяет» до суток без даты «около 09:50» читалось завтрашним утром как прошедшее (ревью
 * 20.10.2026).
 */

import { formatDateTime, formatTime } from '../format';

/** Момент словами: «14:35» — сегодня, иначе «21.10.2026, 14:35». */
export function momentWords(at: string, now: Date = new Date()): string {
  return new Date(at).toDateString() === now.toDateString() ? formatTime(at) : formatDateTime(at);
}

/** Срок повтора: впереди — «срок ещё не настал — повтор сам около 14:35»; прошёл, а повтора ещё
 *  не было, — «срок повтора 14:35 наступил». */
export function termWords(at: string, now: Date = new Date()): string {
  const moment = momentWords(at, now);
  return new Date(at).getTime() <= now.getTime()
    ? `срок повтора ${moment} наступил`
    : `срок ещё не настал — повтор сам около ${moment}`;
}
