/**
 * Отмеченные доноры — набор номеров, который переживает смену страницы,
 * фильтра и заход в карточку.
 *
 * Замечание 26.09.2026: «заведи возможность отмечать, какие именно доноры
 * можно выгрузить». Отметка — не фильтр: в адрес она не пишется (ссылка,
 * отправленная коллеге, не должна приносить чужие отметки), а живёт в кэше
 * запросов под своим ключом. Кэш чистится при выходе целиком — отметки
 * одного человека не достаются следующему, вошедшему в ту же вкладку
 * (урок 19.09.2026: экран показывал предыдущего пользователя).
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';

const PICKS_KEY = ['donor-picks'] as const;

const NOTHING: readonly number[] = [];

export interface Picks {
  picked: ReadonlySet<number>;
  /** Отметить или снять одного. */
  toggle: (id: number) => void;
  /** Отметить или снять всех на странице разом. */
  setAll: (ids: readonly number[], on: boolean) => void;
  clear: () => void;
}

export function usePicks(): Picks {
  const queryClient = useQueryClient();
  // Запроса к серверу нет: данные — только то, что положили руками.
  // Срок и сборка мусора — бесконечные: отметки держатся, пока экран
  // в карточке, а не пять минут умолчания.
  const { data } = useQuery({
    queryKey: PICKS_KEY,
    queryFn: () => NOTHING,
    initialData: NOTHING,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const picked = useMemo(() => new Set(data), [data]);

  const update = useCallback(
    (change: (current: Set<number>) => void) =>
      queryClient.setQueryData<readonly number[]>(PICKS_KEY, (current) => {
        const next = new Set(current ?? NOTHING);
        change(next);
        return [...next];
      }),
    [queryClient],
  );

  const toggle = useCallback(
    (id: number) =>
      update((next) => {
        if (next.has(id)) next.delete(id);
        else next.add(id);
      }),
    [update],
  );
  const setAll = useCallback(
    (ids: readonly number[], on: boolean) =>
      update((next) => {
        for (const id of ids) {
          if (on) next.add(id);
          else next.delete(id);
        }
      }),
    [update],
  );
  const clear = useCallback(() => queryClient.setQueryData(PICKS_KEY, NOTHING), [queryClient]);

  return { picked, toggle, setAll, clear };
}
