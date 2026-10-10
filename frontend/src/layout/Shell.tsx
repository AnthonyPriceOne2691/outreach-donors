/**
 * Рама приложения: кто вошёл, куда можно, выход, тема.
 *
 * В меню показываются только те разделы, на которые у человека есть
 * право. Это удобство, а не защита: сервер всё равно проверит сам,
 * а лишний пункт в меню — это обещание, которое интерфейс не сдержит.
 * У пункта раздела — число работы, которая ждёт там человека (`work.ts`).
 *
 * **Пункт меню — ссылка с адресом**, а не кнопка с переходом по нажатию: его можно
 * открыть в новой вкладке, и диктор называет его ссылкой. «Диалоги» ведут туда,
 * откуда из них ушли (`sectionPlace.ts`, 10.10.2026).
 *
 * **Меню сворачивается до значков** кнопкой в шапке (`navFold.ts`, `NavMenu`,
 * замечание Anthony 10.10.2026). Колонка меняет ширину тем же ходом, что рабочая
 * область — свой отступ (`.mantine-AppShell-navbar` в glass.css): иначе колонка
 * вставала на место сразу, а содержимое доезжало за ней 200 мс и на это время
 * уходило под неё. На телефоне меню выезжает по кнопке и закрывается, когда
 * раздел выбран: прежде оно оставалось поверх открытого раздела.
 *
 * Шапка и боковая колонка — стекло: они стоят поверх полотна, и именно
 * на них держится ощущение глубины. Содержимое — на своей панели, чтобы
 * длинный текст не читался поверх пёстрого пятна фона.
 *
 * Шапка уезжает со страницей, колонка меню остаётся на экране и поднимается
 * к верхнему краю вслед за шапкой (`shellLift.ts`, замечание 25.09.2026).
 */

import { ActionIcon, AppShell, Box, Burger, Button, Group, Title, Tooltip } from '@mantine/core';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
import {
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand,
  IconLogout,
} from '@tabler/icons-react';
import { useEffect, useMemo, useRef } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { SERVICE_NAME } from '../brand';
import { useSession } from '../auth/AuthProvider';
import { AccountMenu } from './AccountMenu';
import { FOLDED_WIDTH, useNavFold } from './navFold';
import { NavMenu } from './NavMenu';
import { navbarWidth } from './navWidth';
import { REMEMBERED, SECTIONS } from './sections';
import { useSectionPlaces } from './sectionPlace';
import { followScroll } from './shellLift';
import { useSplit, workKey } from './split';
import { useWork } from './work';

/** Высота шапки. Одна на раму и на подъём колонки меню: разойдись они —
 *  колонка поднималась бы не до края или заезжала за него. */
const HEADER_HEIGHT = 68;

/** С какой ширины меню — колонкой рядом, а не выезжает по кнопке: `sm` Mantine. */
const BESIDE = '(min-width: 48em)';

/** Кнопка в шапке, сворачивающая меню до значков. Только рядом с колонкой. */
function FoldToggle({ folded, onToggle }: { folded: boolean; onToggle: () => void }) {
  const said = folded ? 'Развернуть меню' : 'Свернуть меню';
  return (
    <Tooltip label={said} withArrow>
      <ActionIcon
        variant="subtle"
        size="lg"
        className="press"
        visibleFrom="sm"
        aria-label={said}
        aria-expanded={!folded}
        aria-controls="app-menu"
        onClick={onToggle}
      >
        {folded ? (
          <IconLayoutSidebarLeftExpand size={20} />
        ) : (
          <IconLayoutSidebarLeftCollapse size={20} />
        )}
      </ActionIcon>
    </Tooltip>
  );
}

export function Shell() {
  const { can, signOut } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const split = useSplit();
  const [opened, { toggle, close }] = useDisclosure();
  const [foldedChoice, toggleFold] = useNavFold();
  // Свёрнутым бывает только меню-колонка: на телефоне оно выезжает с подписями.
  // Значение — с первой отрисовки: иначе свёрнутое меню на каждом заходе
  // рисовалось бы развёрнутым и сворачивалось на глазах (как `useSplit`).
  const beside = useMediaQuery(BESIDE, false, { getInitialValueInEffect: false }) === true;
  const folded = foldedChoice && beside;
  const sections = SECTIONS.filter(
    (section) => section.permission === undefined || can(section.permission),
  );
  const work = useWork(can('view'));
  const placeOf = useSectionPlaces(REMEMBERED);
  const titles = sections.map((section) => section.title).join('\n');
  const counted = sections
    .filter((section) => section.work !== undefined)
    .map((section) => section.title)
    .join('\n');
  const navWidth = useMemo(
    () => navbarWidth(titles.split('\n'), counted.split('\n')),
    [titles, counted],
  );

  // Раздел выбран — выехавшее меню телефона уходит: иначе оно закрывало раздел,
  // пока его не убрали кнопкой.
  useEffect(() => close(), [location.pathname, close]);

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
      navbar={{
        width: folded ? FOLDED_WIDTH : navWidth,
        breakpoint: 'sm',
        collapsed: { mobile: !opened },
      }}
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
            <Burger
              opened={opened}
              onClick={toggle}
              hiddenFrom="sm"
              size="sm"
              aria-label={opened ? 'Закрыть меню' : 'Открыть меню'}
            />
            <FoldToggle folded={foldedChoice} onToggle={toggleFold} />
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

      {/* Имя области: на «Диалогах» рядом вторая навигация — список диалогов. */}
      <AppShell.Navbar p="sm" aria-label="Разделы" id="app-menu">
        <NavMenu sections={sections} work={work} placeOf={placeOf} folded={folded} />
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
