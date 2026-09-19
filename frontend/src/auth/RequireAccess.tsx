/**
 * Три проверки перед тем, как показать экран.
 *
 * Порядок важен и не случаен: сначала «кто ты», потом «сменил ли
 * разовый пароль», и только потом «можно ли тебе сюда». Перепутанный
 * порядок даёт человеку с разовым паролем увидеть экран, на котором
 * ему всё равно откажут.
 *
 * Отсутствие права — это не переадресация, а объяснение: человека,
 * попавшего по ссылке от коллеги, молча выкидывать некуда, и он будет
 * думать, что сервис сломался.
 */

import { Alert, Center, Loader } from '@mantine/core';
import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';

import type { Permission } from '../api/types';
import { useSession } from './AuthProvider';

interface Props {
  children: ReactNode;
  permission?: Permission;
  /** Экран смены пароля — единственный, куда пускают с разовым. */
  allowOneTimePassword?: boolean;
}

export function RequireAccess({ children, permission, allowOneTimePassword = false }: Props) {
  const { user, loading } = useSession();
  const location = useLocation();

  if (loading) {
    return (
      <Center h="60vh">
        <Loader aria-label="Проверяем пропуск" />
      </Center>
    );
  }

  if (user === null) {
    // Куда человек шёл — запоминаем: после входа он вернётся туда же,
    // а не на пустую главную.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  if (user.must_change_password && !allowOneTimePassword) {
    return <Navigate to="/password" replace />;
  }

  if (permission !== undefined && !user.permissions.includes(permission)) {
    return (
      <Alert color="yellow" title="Раздел недоступен" m="md">
        Действие «{permission}» не выдано этой учётке. Права выдаёт админ — он же видит, что именно
        у вас есть.
      </Alert>
    );
  }

  return <>{children}</>;
}
