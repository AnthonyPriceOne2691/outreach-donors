/**
 * Маршруты и обвязка.
 *
 * Гость видит только вход. Всё остальное закрыто `RequireAccess`,
 * и порядок его проверок — вход, разовый пароль, право — описан там же.
 */

import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { BrowserRouter, Route, Routes } from 'react-router-dom';

import { AuthProvider } from './auth/AuthProvider';
import { ChangePasswordPage } from './auth/ChangePasswordPage';
import { LoginPage } from './auth/LoginPage';
import { RequireAccess } from './auth/RequireAccess';
import { Backdrop } from './layout/Backdrop';
import { OverviewPage } from './layout/OverviewPage';
import { Shell } from './layout/Shell';
import { theme } from './theme';
import { UsersPage } from './users/UsersPage';

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      // Повтор запроса по отказу прав или пропуска бессмысленен: ответ
      // не изменится, а в логах сервера это выглядит как перебор.
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/password"
        element={
          <RequireAccess allowOneTimePassword>
            <ChangePasswordPage />
          </RequireAccess>
        }
      />
      <Route
        element={
          <RequireAccess>
            <Shell />
          </RequireAccess>
        }
      >
        <Route path="/" element={<OverviewPage />} />
        <Route
          path="/users"
          element={
            <RequireAccess permission="users">
              <UsersPage />
            </RequireAccess>
          }
        />
      </Route>
    </Routes>
  );
}

export function App() {
  // Клиент создаётся один раз за жизнь приложения. Вызов прямо в разметке
  // делал бы новый кэш на каждую отрисовку — и старый вместе с тем, кто
  // вошёл, оставался бы висеть у прежних наблюдателей.
  const [queryClient] = useState(createQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      {/* Умолчание — как в системе: человек, у которого всё тёмное,
          не должен встречать сервис вспышкой белого. Выбор руками
          Mantine запоминает сам. */}
      <MantineProvider theme={theme} defaultColorScheme="auto">
        <Backdrop />
        {/* Снизу, а не сверху: сверху уведомление накрывало шапку
            с ролью и выходом — видно на снимке первой сборки. */}
        <Notifications position="bottom-right" />
        <AuthProvider>
          <BrowserRouter>
            <AppRoutes />
          </BrowserRouter>
        </AuthProvider>
      </MantineProvider>
    </QueryClientProvider>
  );
}
