/**
 * Рама приложения: кто вошёл, куда можно, выход, тема.
 *
 * В меню показываются только те разделы, на которые у человека есть
 * право. Это удобство, а не защита: сервер всё равно проверит сам,
 * а лишний пункт в меню — это обещание, которое интерфейс не сдержит.
 *
 * Шапка и боковая колонка — стекло: они стоят поверх полотна, и именно
 * на них держится ощущение глубины. Содержимое — на своей панели, чтобы
 * длинный текст не читался поверх пёстрого пятна фона.
 */

import { AppShell, Badge, Burger, Button, Group, NavLink, Stack, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconLogout } from '@tabler/icons-react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { SERVICE_NAME } from '../brand';
import { ROLE_TITLES } from '../api/labels';
import type { Permission } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { ThemeToggle } from './ThemeToggle';

interface Section {
  path: string;
  title: string;
  permission?: Permission;
}

const SECTIONS: Section[] = [
  { path: '/', title: 'Обзор' },
  { path: '/threads', title: 'Диалоги', permission: 'view' },
  { path: '/senders', title: 'Домены рассылки', permission: 'senders' },
  { path: '/users', title: 'Учётки', permission: 'users' },
];

export function Shell() {
  const { user, can, signOut } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [opened, { toggle }] = useDisclosure();

  const leave = () => {
    signOut();
    void navigate('/login', { replace: true });
  };

  return (
    <AppShell
      header={{ height: 68 }}
      navbar={{ width: 232, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding="lg"
      styles={{
        // Рама прозрачна: полотно живёт на `body` и должно просвечивать
        // сквозь всё, иначе стеклу нечего размывать.
        header: { background: 'transparent', border: 'none' },
        navbar: { background: 'transparent', border: 'none' },
        main: { background: 'transparent' },
      }}
    >
      <AppShell.Header p="sm">
        <Group
          h="100%"
          px="md"
          justify="space-between"
          className="glass"
          style={{ height: '100%' }}
        >
          <Group gap="sm">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Title order={4}>{SERVICE_NAME}</Title>
          </Group>
          <Group gap="sm">
            <Text size="sm" c="dimmed" visibleFrom="sm">
              {user?.email}
            </Text>
            <Badge variant="light">{user ? ROLE_TITLES[user.role] : ''}</Badge>
            <Button
              size="compact-sm"
              variant="subtle"
              className="press"
              leftSection={<IconLogout size={16} />}
              onClick={leave}
            >
              Выйти
            </Button>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <Stack h="100%" justify="space-between" className="glassFrame" p="xs" gap="xs">
          <Stack gap={4}>
            {SECTIONS.filter(
              (section) => section.permission === undefined || can(section.permission),
            ).map((section) => (
              <NavLink
                key={section.path}
                label={section.title}
                className="glassSlot"
                active={
                  section.path === '/'
                    ? location.pathname === '/'
                    : location.pathname.startsWith(section.path)
                }
                variant="light"
                onClick={() => void navigate(section.path)}
              />
            ))}
          </Stack>

          {/* Переключатель темы отделён линией: без неё он читается ещё
              одним пунктом меню. */}
          <Stack gap="xs" className="hairline" pt="xs">
            <ThemeToggle />
          </Stack>
        </Stack>
      </AppShell.Navbar>

      <AppShell.Main>
        {/* Ключ по пути: без него подъём играет один раз за жизнь рамы,
            и переход между экранами выглядит подменой картинки. */}
        <div className="riseIn" key={location.pathname}>
          <Outlet />
        </div>
      </AppShell.Main>
    </AppShell>
  );
}
