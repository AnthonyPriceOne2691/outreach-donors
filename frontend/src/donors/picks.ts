/**
 * Отмеченные доноры — набор номеров, который переживает смену страницы,
 * фильтра и заход в карточку.
 *
 * Замечание 26.09.2026: «заведи возможность отмечать, какие именно доноры
 * можно выгрузить». Отметка — не фильтр: в адрес она не пишется (ссылка,
 * отправленная коллеге, не должна приносить чужие отметки), а живёт в памяти
 * вкладки — рядом с клиентом запросов и под учёткой вошедшего. Другая
 * учётка — другой набор: отметки одного человека не достаются следующему,
 * вошедшему в ту же вкладку (урок 19.09.2026: экран показывал предыдущего
 * пользователя). Владелец — номер учётки, а не строка пропуска: смена своего
 * пароля выдаёт новый пропуск, и отметки того же человека пропадали бы с ним
 * (ревью продаж 11.10.2026).
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

import { useSession } from '../auth/AuthProvider';

interface Store {
  /** Учётка, под которой отмечали: сменилась — отметки не наши. */
  owner: number | null;
  picked: ReadonlySet<number>;
  listeners: Set<() => void>;
}

/** Набор на клиента запросов: у каждого приложения (и у каждого теста) свой. */
const stores = new WeakMap<QueryClient, Store>();

function storeOf(client: QueryClient, owner: number | null): Store {
  let store = stores.get(client);
  if (store === undefined) {
    store = { owner, picked: new Set(), listeners: new Set() };
    stores.set(client, store);
  }
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
  const owner = useSession().user?.id ?? null;
  const subscribe = useCallback(
    (listener: () => void) => {
      const { listeners } = storeOf(client, owner);
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    [client, owner],
  );
  const picked = useSyncExternalStore(subscribe, () => storeOf(client, owner).picked);

  const update = useCallback(
    (change: (next: Set<number>) => void) => {
      const store = storeOf(client, owner);
      const next = new Set(store.picked);
      change(next);
      store.picked = next;
      for (const listener of store.listeners) listener();
    },
    [client, owner],
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
