/**
 * Фильтры и страница списка — в адресе: ссылка открывает тот же вопрос, обновление
 * и «назад» его не теряют. Одно правило на списки с фильтрами — доноры, отбор,
 * рекламодатели; до 12.10.2026 каждый держал его своей копией.
 *
 * - Смена фильтра — первая страница и замена записи в истории: «назад» ведёт туда,
 *   откуда пришли, а не по буквам поиска, а двадцатая страница старого вопроса ничего
 *   не говорит о новом.
 * - Страница — новая запись; страница за концом из старой ссылки — последняя
 *   настоящая, заменой записи (`useLastPage`).
 * - Сброс — замена: экран тот же, вопрос другой.
 *
 * `read` и `write` — разбор и запись адреса своего списка (`donorFilters.ts`,
 * `selectionFilters.ts`, `listFilters.ts`), функции уровня модуля: от них зависят
 * функции, которые отдаёт хук.
 */

import { useCallback, useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

export function useUrlFilters<F extends { page: number }>(
  read: (params: URLSearchParams) => F,
  write: (filters: F) => URLSearchParams,
) {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => read(params), [read, params]);
  const apply = useCallback(
    (patch: Partial<F>) =>
      setParams((current) => write({ ...read(current), ...patch, page: 1 }), { replace: true }),
    [read, write, setParams],
  );
  const turn = useCallback(
    (page: number, replace = false) =>
      setParams((current) => write({ ...read(current), page }), { replace }),
    [read, write, setParams],
  );
  const reset = useCallback((to: F) => setParams(write(to), { replace: true }), [write, setParams]);
  return { filters, apply, turn, reset };
}

/** Ответ сервера на страницу списка — по нему видно, что она за концом. */
interface Answer {
  rows: readonly unknown[];
  total: number;
}

/**
 * Страница из старой ссылки может оказаться за концом: вместо пустоты с «ничего не
 * нашлось» — последняя настоящая страница. `answer` — ответ на этот вопрос, когда он
 * улёгся (пока едет новый, прежний ответ не в счёт); `last` — последняя страница.
 */
export function useLastPage(
  page: number,
  answer: Answer | undefined,
  last: number,
  turn: (page: number, replace?: boolean) => void,
) {
  useEffect(() => {
    if (answer !== undefined && answer.rows.length === 0 && answer.total > 0 && page > last) {
      turn(last, true);
    }
  }, [answer, page, last, turn]);
}
