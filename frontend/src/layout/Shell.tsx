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
 *
 * Шапка уезжает со страницей, колонка меню остаётся на экране и поднимается
 * к верхнему краю вслед за шапкой (`shellLift.ts`, замечание 25.09.2026).
 */

import { AppShell, Badge, Burger, Button, Group, NavLink, Stack, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconLogout } from '@tabler/icons-react';
import { useEffect, useMemo, useRef } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { SERVICE_NAME } from '../brand';
import { ROLE_TITLES } from '../api/labels';
import type { Permission } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { navbarWidth } from './navWidth';
import { followScroll } from './shellLift';
import { ThemeToggle } from './ThemeToggle';

interface Section {
  path: string;
  title: string;
  permission?: Permission;
}

const SECTIONS: Section[] = [
  { path: '/', title: 'Обзор' },
  { path: '/run', title: 'Прогон', permission: 'view' },
  { path: '/donors', title: 'Доноры', permission: 'view' },
  { path: '/selection', title: 'Отбор', permission: 'view' },
  { path: '/forms', title: 'Формы', permission: 'view' },
  { path: '/advertisers', title: 'Рекламодатели', permission: 'view' },
  { path: '/letters', title: 'Письма', permission: 'view' },
  { path: '/threads', title: 'Диалоги', permission: 'view' },
  { path: '/suppressions', title: 'Стоп-лист', permission: 'view' },
  { path: '/settings', title: 'Пороги', permission: 'view' },
  { path: '/usage', title: 'Расход', permission: 'view' },
  { path: '/senders', title: 'Домены рассылки', permission: 'senders' },
  { path: '/users', title: 'Учётки', permission: 'users' },
];

/** Высота шапки. Одна на раму и на подъём колонки меню: разойдись они —
 *  колонка поднималась бы не до края или заезжала за него. */
const HEADER_HEIGHT = 68;

export function Shell() {
  const { user, can, signOut } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [opened, { toggle }] = useDisclosure();
  const sections = SECTIONS.filter(
    (section) => section.permission === undefined || can(section.permission),
  );
  const titles = sections.map((section) => section.title).join('\n');
  const navWidth = useMemo(() => navbarWidth(titles.split('\n')), [titles]);

  const leave = () => {
    signOut();
    void navigate('/login', { replace: true });
  };

  const frame = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (frame.current === null) return undefined;
    return followScroll(frame.current, HEADER_HEIGHT);
  }, []);

  return (
    <AppShell
      ref={frame}
      header={{ height: HEADER_HEIGHT }}
      navbar={{ width: navWidth, breakpoint: 'sm', collapsed: { mobile: !opened } }}
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
          wrap="nowrap"
          className="glass"
          style={{ height: '100%' }}
        >
          {/* Обе половины шапки не переносятся: на узком окне роль и выход
              выпадали под шапку, за пределы стекла. Имя сервиса ужимается,
              роль на телефоне не показывается — она есть в «Обзоре». */}
          <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Title order={4} style={{ whiteSpace: 'nowrap' }}>
              {SERVICE_NAME}
            </Title>
          </Group>
          <Group gap="sm" wrap="nowrap" style={{ flexShrink: 0 }}>
            <Text size="sm" c="dimmed" visibleFrom="md">
              {user?.email}
            </Text>
            <Badge variant="light" visibleFrom="sm">
              {user ? ROLE_TITLES[user.role] : ''}
            </Badge>
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
        <Stack h="100%" justify="space-between" className="glassFrame navFrame" p="xs" gap="xs">
          <Stack gap={4}>
            {sections.map((section) => (
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
        {/* Ширина рабочей области — одна на все экраны, а не своя у каждого:
            экраны шириной 1010, 1230 и 1420 пикселей подряд читаются как
            прыгающая рама. */}
        <div className="riseIn workArea" key={location.pathname}>
          <Outlet />
        </div>
      </AppShell.Main>
    </AppShell>
  );
}
