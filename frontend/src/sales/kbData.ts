/**
 * Запросы вкладок «База знаний» и «Отправитель»: ключи кэша, чтение и правка.
 *
 * Одно место на обе вкладки и на страницу раздела: список базы спрашивают и
 * страница (число на вкладке), и вкладка — ключ один, запрос один. Правка
 * перечитывает базу целиком, и предпросмотр агента (ключ под тем же префиксом)
 * — вместе с ней: после выключения записи окно «что увидит агент» не покажет
 * прежний ответ.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { changeKbEntry, listKb, readSender, saveSender } from '../api/sales';
import type { KbEntryCard } from '../api/salesTypes';
import { notify } from '../notices';

export const KB_QUERY_KEY = ['sales', 'kb'] as const;
export const SENDER_QUERY_KEY = ['sales', 'sender'] as const;

export function useKb() {
  return useQuery({ queryKey: KB_QUERY_KEY, queryFn: listKb });
}

/** Включить или выключить запись. Отказ — уведомлением со словами сервера. */
export function useToggle() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) => changeKbEntry(id, { active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: KB_QUERY_KEY }),
    onError: (failure) =>
      notify({ title: 'Не переключили', message: refusalOf(failure), color: 'red' }),
  });
}

/** Запись сохранена в окне: перечитать базу и сказать об этом. */
export function useSavedEntry() {
  const queryClient = useQueryClient();
  return async (card: KbEntryCard) => {
    await queryClient.invalidateQueries({ queryKey: KB_QUERY_KEY });
    notify({ message: `Запись «${card.title}» сохранена`, color: 'green' });
  };
}

export function useSender() {
  return useQuery({ queryKey: SENDER_QUERY_KEY, queryFn: readSender });
}

/** Записать отправителя: ответ сервера сразу становится прочитанным — с готовностью. */
export function useSaveSender() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: saveSender,
    onSuccess: (saved) => {
      queryClient.setQueryData(SENDER_QUERY_KEY, saved);
      notify({ message: 'Отправитель сохранён', color: 'green' });
    },
  });
}
