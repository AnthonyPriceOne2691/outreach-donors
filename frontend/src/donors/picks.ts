/**
 * Отмеченные доноры — набор номеров, который переживает смену страницы,
 * фильтра и заход в карточку.
 *
 * Замечание 26.09.2026: «заведи возможность отмечать, какие именно доноры
 * можно выгрузить». Отметка — не фильтр: в адрес она не пишется (ссылка,
 * отправленная коллеге, не должна приносить чужие отметки), а живёт в памяти
 * вкладки — рядом с клиентом запросов и под пропуском вошедшего. Другой
 * пропуск — другой набор: отметки одного человека не достаются следующему,
 * вошедшему в ту же вкладку (урок 19.09.2026: экран показывал предыдущего
 * пользователя).
 *
 * **Отметка ставится сразу, а не после.** Первая версия держала набор
 * в кэше запросов, и тот сообщает об изменении не сразу, а следующей задачей:
 * между щелчком и перерисовкой флажок успевал вернуться в «не отмечен»,
 * и проверка браузером ловила это как «щелчок не изменил состояние». Здесь
 * хранилище своё и синхронное (`useSyncExternalStore`).
 */

import { useQueryClient } from '@tanstack/react-query';
import type { QueryClient } from '@tanstack/react-query';
import { useCallback, useMemo, useSyncExternalStore } from 'react';

import { readToken } from '../auth/session';

interface Store {
  /** Пропуск, под которым отмечали: сменился — отметки не наши. */
  owner: string | null;
  picked: ReadonlySet<number>;
  listeners: Set<() => void>;
}

/** Набор на клиента запросов: у каждого приложения (и у каждого теста) свой. */
const stores = new WeakMap<QueryClient, Store>();

function storeOf(client: QueryClient): Store {
  let store = stores.get(client);
  if (store === undefined) {
    store = { owner: readToken(), picked: new Set(), listeners: new Set() };
    stores.set(client, store);
  }
  const owner = readToken();
  if (owner !== store.owner) {
    store.owner = owner;
    store.picked = new Set();
  }
  return store;
}

export interface Picks {
  picked: ReadonlySet<number>;
  /** Отметить или снять одного. */
  toggle: (id: number) => void;
  /** Отметить или снять всех на странице разом. */
  setAll: (ids: readonly number[], on: boolean) => void;
  clear: () => void;
}

export function usePicks(): Picks {
  const client = useQueryClient();
  const subscribe = useCallback(
    (listener: () => void) => {
      const { listeners } = storeOf(client);
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    [client],
  );
  const picked = useSyncExternalStore(subscribe, () => storeOf(client).picked);

  const update = useCallback(
    (change: (next: Set<number>) => void) => {
      const store = storeOf(client);
      const next = new Set(store.picked);
      change(next);
      store.picked = next;
      for (const listener of store.listeners) listener();
    },
    [client],
  );

  return useMemo(
    () => ({
      picked,
      toggle: (id: number) =>
        update((next) => {
          if (next.has(id)) next.delete(id);
          else next.add(id);
        }),
      setAll: (ids: readonly number[], on: boolean) =>
        update((next) => {
          for (const id of ids) {
            if (on) next.add(id);
            else next.delete(id);
          }
        }),
      clear: () => update((next) => next.clear()),
    }),
    [picked, update],
  );
}
