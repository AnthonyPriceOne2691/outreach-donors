/**
 * Что попадёт в файл выгрузки — словами на кнопке и в уведомлении после.
 *
 * Замечание 26.09.2026: «синхронизируй кнопку „выгрузить все“ с состоянием,
 * когда отмечено несколько, и когда человек отфильтровал и хочет выгрузить
 * сразу всех отфильтрованных». Кнопка одна, и её подпись говорит ровно то,
 * что ляжет в файл:
 *
 * - ничего не отмечено, фильтров нет — «Выгрузить всех · 612»;
 * - ничего не отмечено, фильтр есть — «Выгрузить найденных · 45»;
 * - отмечено — «Выгрузить отмеченных · 3»: отмеченные — независимо от фильтра.
 *
 * Найдено больше, чем выгрузка кладёт в файл за раз, — подпись называет обе
 * цифры, а не обещает всех: потолок приходит с сервера (`export_limit`),
 * своей копии числа здесь нет. Уведомление после выгрузки говорит, сколько
 * строк легло в файл на самом деле, — по заголовкам ответа, а не по догадке.
 */

import type { ExportCounts } from '../api/donors';
import { formatNumber, plural } from '../format';

export interface ExportPlan {
  label: string;
  /** Выгружать нечего — кнопка выключена, а не приносит пустой файл. */
  empty: boolean;
  /** Выгружаются отмеченные, а не найденные фильтром. */
  picked: boolean;
}

interface Situation {
  /** Сколько отмечено. */
  picked: number;
  /** Сужен ли список фильтром. */
  filtered: boolean;
  /** Сколько найдено фильтром (без него — всего доноров). */
  found: number;
  /** Сколько строк выгрузка кладёт в файл за раз. */
  limit: number;
}

export function exportPlan({ picked, filtered, found, limit }: Situation): ExportPlan {
  if (picked > 0) {
    return { label: `Выгрузить отмеченных · ${formatNumber(picked)}`, empty: false, picked: true };
  }
  if (found > limit) {
    return {
      label: `Выгрузить ${formatNumber(limit)} из ${formatNumber(found)}`,
      empty: false,
      picked: false,
    };
  }
  return {
    label: `Выгрузить ${filtered ? 'найденных' : 'всех'} · ${formatNumber(found)}`,
    empty: found === 0,
    picked: false,
  };
}

function donors(count: number): string {
  return plural(count, 'донор', 'донора', 'доноров');
}

/** Что легло в файл — словами для уведомления после выгрузки. */
export function exportOutcome(counts: ExportCounts, picked: boolean): string {
  const { rows, asked } = counts;
  if (rows === null) return 'Файл выгружен.';
  if (asked === null || rows >= asked) {
    return picked
      ? `Выгружено отмеченных: ${formatNumber(rows)}.`
      : `Выгружено: ${formatNumber(rows)} ${donors(rows)}.`;
  }
  if (!picked) {
    return (
      `В файле ${formatNumber(rows)} из ${formatNumber(asked)}: больше за раз не выгружается — ` +
      'сузьте фильтр, чтобы выгрузить остальных.'
    );
  }
  const reasons: string[] = [];
  if ((counts.notDonors ?? 0) > 0) {
    reasons.push(`${formatNumber(counts.notDonors)} уже не доноры — решение человека сменилось`);
  }
  if ((counts.missing ?? 0) > 0) {
    reasons.push(`${formatNumber(counts.missing)} больше нет в базе`);
  }
  const why = reasons.length > 0 ? `: ${reasons.join('; ')}` : '';
  return `В файле ${formatNumber(rows)} из ${formatNumber(asked)} отмеченных${why}.`;
}
