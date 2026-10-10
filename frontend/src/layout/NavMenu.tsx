/**
 * Колонка меню: группы разделов, у каждого пункта — значок и число ждущей работы,
 * внизу — переключатель темы.
 *
 * **Свёрнутое меню — одни значки** (`navFold.ts`, замечание Anthony 10.10.2026).
 * Название пункта при этом не пропадает: диктор читает его из ссылки, глазу оно —
 * во всплывающей подсказке справа от значка. Число ждущей работы — жёлтой точкой на
 * значке, а сколько именно — в той же подсказке: трёхзначное число на значке
 * в 20 px не читается. Подписи групп свёрнуты в линию.
 */

import { Badge, Indicator, NavLink, Stack, Tooltip, VisuallyHidden } from '@mantine/core';
import { Link, useLocation } from 'react-router-dom';

import type { WorkView } from '../api/overview';
import { formatNumber } from '../format';
import { NAV_GROUPS, NavGroup } from './NavGroup';
import type { Section } from './sections';
import { ThemeToggle } from './ThemeToggle';

/** Больше значок не показывает: место под число у пункта — на три цифры (`navWidth.ts`). */
const COUNT_CAP = 999;

const capped = (count: number) => (count > COUNT_CAP ? `${COUNT_CAP}+` : formatNumber(count));

/** Число у пункта: работы нет — значка нет, ноль не рисуется. Точное число
 *  сверх потолка — на «Обзоре» и на экране раздела. */
function WorkCount({ count }: { count: number | undefined }) {
  if (count === undefined || count === 0) return null;
  return (
    <Badge size="sm" variant="light" color="yellow">
      {capped(count)}
      <VisuallyHidden> ждут человека</VisuallyHidden>
    </Badge>
  );
}

interface ItemProps {
  section: Section;
  count: number | undefined;
  to: string;
  active: boolean;
  folded: boolean;
}

function NavItem({ section, count, to, active, folded }: ItemProps) {
  const Icon = section.icon;
  const waiting = count !== undefined && count > 0;
  const icon = <Icon size={20} stroke={1.6} aria-hidden />;
  const said = waiting ? `${section.title} · ${capped(count)} ждут человека` : section.title;
  const link = (
    <NavLink
      label={folded ? <VisuallyHidden>{said}</VisuallyHidden> : section.title}
      leftSection={
        folded && waiting ? (
          <Indicator size={8} color="yellow" offset={2}>
            {icon}
          </Indicator>
        ) : (
          icon
        )
      }
      rightSection={folded ? null : <WorkCount count={count} />}
      className="glassSlot"
      active={active}
      variant="light"
      component={Link}
      to={to}
    />
  );
  if (!folded) return link;
  return (
    <Tooltip label={said} position="right" withArrow openDelay={150}>
      {link}
    </Tooltip>
  );
}

interface Props {
  sections: Section[];
  work: WorkView | undefined;
  placeOf: (path: string) => string;
  folded: boolean;
}

export function NavMenu({ sections, work, placeOf, folded }: Props) {
  const location = useLocation();
  const isActive = (path: string) =>
    path === '/' ? location.pathname === '/' : location.pathname.startsWith(path);
  return (
    <Stack
      h="100%"
      justify="space-between"
      className="glassFrame navFrame"
      p="xs"
      gap="xs"
      data-folded={folded || undefined}
    >
      {/* Пункты — своей прокруткой, переключатель темы — внизу рамы всегда: с
          подписями групп пятнадцать пунктов не помещались в колонку на 1280 × 800,
          и переключатель уходил под край (аудит экранов 09.10.2026). */}
      <Stack gap="md" className="navScroll">
        {NAV_GROUPS.map((group) => {
          const items = sections.filter((section) => section.group === group.key);
          if (items.length === 0) return null;
          return (
            <NavGroup key={group.key} group={group} folded={folded}>
              {items.map((section) => (
                <NavItem
                  key={section.path}
                  section={section}
                  count={section.work === undefined ? undefined : work?.[section.work]}
                  to={placeOf(section.path)}
                  active={isActive(section.path)}
                  folded={folded}
                />
              ))}
            </NavGroup>
          );
        })}
      </Stack>

      {/* Переключатель темы отделён линией: без неё он читается ещё
          одним пунктом меню. В свёрнутом меню — столбиком: в строку три значка
          в колонку шириной со значок не встают. */}
      <Stack gap="xs" className="hairline" pt="xs">
        <ThemeToggle vertical={folded} />
      </Stack>
    </Stack>
  );
}
