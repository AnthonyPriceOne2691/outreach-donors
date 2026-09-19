/**
 * Рама приложения: кто вошёл, куда можно, выход.
 *
 * В меню показываются только те разделы, на которые у человека есть
 * право. Это удобство, а не защита: сервер всё равно проверит сам,
 * а лишний пункт в меню — это обещание, которое интерфейс не сдержит.
 */

import { AppShell, Badge, Burger, Button, Group, NavLink, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import type { Permission } from '../api/types';
import { useSession } from '../auth/AuthProvider';

interface Section {
  path: string;
  title: string;
  permission?: Permission;
}

const SECTIONS: Section[] = [
  { path: '/', title: 'Обзор' },
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
      header={{ height: 56 }}
      navbar={{ width: 220, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Title order={4}>Доноры и цены</Title>
          </Group>
          <Group gap="xs">
            <Text size="sm">{user?.email}</Text>
            <Badge variant="light">{user?.role === 'admin' ? 'админ' : 'оператор'}</Badge>
            <Button size="compact-sm" variant="default" onClick={leave}>
              Выйти
            </Button>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        {SECTIONS.filter(
          (section) => section.permission === undefined || can(section.permission),
        ).map((section) => (
          <NavLink
            key={section.path}
            label={section.title}
            active={location.pathname === section.path}
            onClick={() => void navigate(section.path)}
          />
        ))}
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
