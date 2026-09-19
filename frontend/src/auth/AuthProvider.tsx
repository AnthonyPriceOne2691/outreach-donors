/**
 * Кто вошёл — один источник на всё приложение.
 *
 * Права берутся из ответа сервера `/api/auth/me`, а не выводятся из роли
 * на фронте: иначе матрица прав существует в двух местах и однажды
 * расходится — кнопка есть, а запрос отказывает.
 *
 * **Права в интерфейсе — это удобство, а не защита.** Скрытая кнопка
 * не мешает отправить запрос руками; единственная настоящая проверка
 * живёт на сервере. Здесь мы только не показываем человеку того, чего
 * он всё равно не сможет сделать.
 *
 * **Карточка лежит в кэше под пропуском, а не под общим ключом.**
 * Найдено живым прогоном 19.09.2026: после выхода и входа другим
 * человеком экран показывал предыдущего — его почту, роль и меню, —
 * хотя запросы уже уходили с новым пропуском. Причина: выход чистил
 * кэш целиком, а наблюдатель за очищенным запросом оставался с прежним
 * ответом, и перерисовать его было некому — `AuthProvider` стоит над
 * маршрутизатором и переходами не задевается. Пропуск в ключе закрывает
 * этот класс по построению: чужая карточка под чужим ключом и показана
 * быть не может.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createContext, use, useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { fetchMe, login as loginRequest } from '../api/auth';
import { AuthError } from '../api/client';
import type { Me, Permission } from '../api/types';
import { clearToken, readToken, saveToken, watchTokenLoss } from './session';

interface Session {
  user: Me | null;
  /** Первая проверка пропуска ещё идёт — показывать интерфейс рано. */
  loading: boolean;
  can: (permission: Permission) => boolean;
  signIn: (email: string, password: string) => Promise<Me>;
  signOut: () => void;
  /** Перечитать себя: после смены пароля и после правки своих прав. */
  refresh: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

export const meQueryKey = (token: string | null) => ['me', token] as const;

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  // Пропуск держится состоянием, а не читается из хранилища при каждой
  // отрисовке: смена пропуска обязана перерисовать всё дерево, иначе
  // новый человек видит экран старого.
  const [token, setToken] = useState<string | null>(() => readToken());

  const { data, isLoading } = useQuery({
    queryKey: meQueryKey(token),
    queryFn: fetchMe,
    // Пропуска нет — спрашивать некого. Запрос всё равно ушёл бы и вернул
    // 401, и в логах сервера это выглядело бы как попытка входа.
    enabled: token !== null,
    retry: (count, error) => !(error instanceof AuthError) && count < 2,
    staleTime: 60_000,
  });

  const signIn = useCallback(
    async (email: string, password: string) => {
      const opened = await loginRequest(email, password);
      saveToken(opened.token);
      setToken(opened.token);
      // Карточка приходит вместе с пропуском — лишний запрос за ней дал бы
      // промежуток, в котором непонятно, что рисовать.
      queryClient.setQueryData(meQueryKey(opened.token), opened.user);
      return opened.user;
    },
    [queryClient],
  );

  const signOut = useCallback(() => {
    clearToken();
    setToken(null);
    // Чистится весь кэш, а не только «кто я»: в нём лежат данные,
    // которых следующему вошедшему видеть не положено.
    queryClient.clear();
  }, [queryClient]);

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: meQueryKey(token) });
  }, [queryClient, token]);

  useEffect(() => watchTokenLoss(() => setToken(null)), []);

  const value = useMemo<Session>(
    () => ({
      user: data ?? null,
      loading: isLoading,
      can: (permission) => data?.permissions.includes(permission) ?? false,
      signIn,
      signOut,
      refresh,
    }),
    [data, isLoading, signIn, signOut, refresh],
  );

  return <SessionContext value={value}>{children}</SessionContext>;
}

export function useSession(): Session {
  const session = use(SessionContext);
  if (session === null) {
    throw new Error('useSession вызван вне AuthProvider — обёртка потерялась при правке маршрутов');
  }
  return session;
}
