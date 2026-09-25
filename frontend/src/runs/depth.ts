/**
 * Глубина выдачи — сколько результатов берём по каждому ключу.
 *
 * Замечание 25.09.2026: поле «Глубина, страниц» (от одной до пяти, «10
 * результатов на странице») стало выпадающим списком «Глубина выдачи»
 * на 10, 20, 30, 50 и 100 результатов, по умолчанию 10.
 *
 * **На сервер уходят страницы, а не результаты** (`depth_pages`): провайдер
 * берёт деньги за каждые десять, и смета считает в них же. Перевод живёт
 * здесь, одним местом; сервер принимает от одной до десяти страниц и за
 * пределом отказывает словами, называя обе единицы.
 */

/** Результатов на странице выдачи — так её считает и провайдер, и смета. */
export const RESULTS_PER_PAGE = 10;

/** Что предлагает список, в результатах. */
export const DEPTHS = [10, 20, 30, 50, 100] as const;

export type Depth = (typeof DEPTHS)[number];

/** Умолчание — топ-10: глубже дороже вдесятеро, и выбирают это сознательно. */
export const DEFAULT_DEPTH: Depth = 10;

export function pagesOf(results: Depth): number {
  return results / RESULTS_PER_PAGE;
}

export function depthTitle(results: number): string {
  return `${results} результатов`;
}

/** «2 ключа», «5 ключей», «21 ключ» — число с правильным словом. */
export function keywordsTitle(count: number): string {
  const tens = count % 100;
  const ones = count % 10;
  if (tens >= 11 && tens <= 14) return `${count} ключей`;
  if (ones === 1) return `${count} ключ`;
  if (ones >= 2 && ones <= 4) return `${count} ключа`;
  return `${count} ключей`;
}
