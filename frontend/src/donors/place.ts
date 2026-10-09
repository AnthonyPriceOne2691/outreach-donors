/**
 * Где донор стоит в списке, из которого открыли его карточку: адрес списка с
 * фильтрами — для «К списку», порядок строк страницы — для ‹ › к соседнему донору
 * (аудит экранов 09.10.2026: чтобы посмотреть следующего, возвращались к списку и
 * искали строку глазами). Соседи — в пределах открытой страницы списка: её строки
 * и видел человек.
 */

export interface DonorPlace {
  from: string;
  ids: number[];
}

export function placeOf(from: string, rows: { id: number }[]): DonorPlace {
  return { from, ids: rows.map((row) => row.id) };
}

/** Соседи донора в списке, из которого пришли. Пришли не из списка — соседей нет. */
export function neighboursOf(
  state: unknown,
  id: number,
): { prev: number | null; next: number | null } {
  const ids =
    state && typeof state === 'object' && 'ids' in state && Array.isArray(state.ids)
      ? state.ids.filter((one): one is number => typeof one === 'number')
      : [];
  const at = ids.indexOf(id);
  if (at === -1) return { prev: null, next: null };
  return { prev: ids[at - 1] ?? null, next: ids[at + 1] ?? null };
}
