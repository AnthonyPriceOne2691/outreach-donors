/**
 * Рама приложения: кто вошёл, куда можно, выход, тема.
 *
 * В меню показываются только те разделы, на которые у человека есть
 * право. Это удобство, а не защита: сервер всё равно проверит сам,
 * а лишний пункт в меню — это обещание, которое интерфейс не сдержит.
 * У пункта раздела — число работы, которая ждёт там человека (`work.ts`).
 *
 * Шапка и боковая колонка — стекло: они стоят поверх полотна, и именно
 * на них держится ощущение глубины. Содержимое — на своей панели, чтобы
 * длинный текст не читался поверх пёстрого пятна фона.
 *
 * Шапка уезжает со страницей, колонка меню остаётся на экране и поднимается
 * к верхнему краю вслед за шапкой (`shellLift.ts`, замечание 25.09.2026).
 */

import {
  AppShell,
  Badge,
  Box,
  Burger,
  Button,
  Group,
  NavLink,
  Stack,
  Title,
  VisuallyHidden,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconLogout } from '@tabler/icons-react';
import { useEffect, useMemo, useRef } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { SERVICE_NAME } from '../brand';
import type { Permission } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatNumber } from '../format';
import { AccountMenu } from './AccountMenu';
import { navbarWidth } from './navWidth';
import { followScroll } from './shellLift';
import { useSplit, workKey } from './split';
import { ThemeToggle } from './ThemeToggle';
import { useWork } from './work';
import type { WorkSection } from './work';

interface Section {
  path: string;
  title: string;
  permission?: Permission;
  /** Какое число ждущей работы стоит у пункта. */
  work?: WorkSection;
}

const SECTIONS: Section[] = [
  { path: '/', title: 'Обзор' },
  { path: '/run', title: 'Прогон', permission: 'view', work: 'run' },
  { path: '/donors', title: 'Доноры', permission: 'view' },
  { path: '/selection', title: 'Отбор', permission: 'view' },
  { path: '/forms', title: 'Формы', permission: 'view', work: 'forms' },
  { path: '/advertisers', title: 'Рекламодатели', permission: 'view', work: 'advertisers' },
  // Своё право, а не `view`: раздел снимается с учётки поимённо (решение владельца 01.10).
  { path: '/sales', title: 'Продажи', permission: 'sales' },
  { path: '/letters', title: 'Письма', permission: 'view' },
  { path: '/threads', title: 'Диалоги', permission: 'view', work: 'threads' },
  { path: '/suppressions', title: 'Стоп-лист', permission: 'view' },
  { path: '/settings', title: 'Пороги', permission: 'view' },
  { path: '/agent', title: 'Агент переписки', permission: 'view' },
  { path: '/usage', title: 'Расход', permission: 'view' },
  { path: '/senders', title: 'Домены рассылки', permission: 'senders' },
  { path: '/users', title: 'Учётки', permission: 'users' },
];

/** Высота шапки. Одна на раму и на подъём колонки меню: разойдись они —
 *  колонка поднималась бы не до края или заезжала за него. */
const HEADER_HEIGHT = 68;

/** Больше значок не показывает: место под число у пункта — на три цифры (`navWidth.ts`). */
const COUNT_CAP = 999;

/** Число у пункта: работы нет — значка нет, ноль не рисуется. Точное число
 *  сверх потолка — на «Обзоре» и на экране раздела. */
function WorkCount({ count }: { count: number | undefined }) {
  if (count === undefined || count === 0) return null;
  return (
    <Badge size="sm" variant="light" color="yellow">
      {count > COUNT_CAP ? `${COUNT_CAP}+` : formatNumber(count)}
      <VisuallyHidden> ждут человека</VisuallyHidden>
    </Badge>
  );
}

export function Shell() {
  const { can, signOut } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const split = useSplit();
  const [opened, { toggle }] = useDisclosure();
  const sections = SECTIONS.filter(
    (section) => section.permission === undefined || can(section.permission),
  );
  const work = useWork(can('view'));
  const titles = sections.map((section) => section.title).join('\n');
  const counted = sections
    .filter((section) => section.work !== undefined)
    .map((section) => section.title)
    .join('\n');
  const navWidth = useMemo(
    () => navbarWidth(titles.split('\n'), counted.split('\n')),
    [titles, counted],
  );

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
              выпадали под шапку, за пределы стекла. Имя сервиса ужимается до
              многоточия, а не уходит под кнопки; на телефоне почта и «Выйти» —
              значками с теми же названиями. Роль и права — в меню у почты. */}
          <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Title
              order={4}
              style={{
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                minWidth: 0,
              }}
            >
              {SERVICE_NAME}
            </Title>
          </Group>
          <Group gap="xs" wrap="nowrap" style={{ flexShrink: 0 }}>
            <AccountMenu />
            <Button
              size="compact-sm"
              variant="subtle"
              className="press"
              leftSection={<IconLogout size={16} />}
              aria-label="Выйти"
              onClick={leave}
            >
              <Box component="span" visibleFrom="sm">
                Выйти
              </Box>
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
                rightSection={
                  section.work === undefined ? null : <WorkCount count={work?.[section.work]} />
                }
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
            и переход между экранами выглядит подменой картинки. У раздела со
            списком рядом с записью — по разделу (`layout/split`). */}
        {/* Ширина рабочей области — одна на все экраны, а не своя у каждого:
            экраны шириной 1010, 1230 и 1420 пикселей подряд читаются как
            прыгающая рама. */}
        <div className="riseIn workArea" key={workKey(location.pathname, split)}>
          <Outlet />
        </div>
      </AppShell.Main>
    </AppShell>
  );
}
