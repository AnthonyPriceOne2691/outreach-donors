/**
 * Колонка меню: группы разделов, у каждого пункта — значок и число ждущей работы,
 * внизу — переключатель темы.
 *
 * **Свёрнутое меню — одни значки** (`navFold.ts`, замечание Anthony 10.10.2026).
 * Название пункта при этом не пропадает: диктор читает его из ссылки, глазу оно —
 * во всплывающей подсказке справа от значка. Число ждущей работы — жёлтой точкой на
 * значке, а сколько именно — в той же подсказке: трёхзначное число на значке
 * в 20 px не читается. Подписи групп свёрнуты в линию.
 *
 * **Подсвечен один пункт — тот, чей адрес открыт** (`sections.activeKey`): «Диалоги»
 * доноров, рекламодателей и «Диалоги продаж» — один путь, и различает их часть в адресе
 * (`?stage=…`).
 */

import { Badge, Indicator, NavLink, Stack, Tooltip, VisuallyHidden } from '@mantine/core';
import { Link, useLocation } from 'react-router-dom';

import type { WorkView } from '../api/overview';
import { formatNumber } from '../format';
import { NAV_GROUPS, NavGroup } from './NavGroup';
import { activeKey, sectionKey } from './sections';
import type { Section } from './sections';
import { ThemeToggle } from './ThemeToggle';

/** Больше значок не показывает: место под число у пункта — на три цифры (`navWidth.ts`). */
const COUNT_CAP = 999;

const capped = (count: number) => (count > COUNT_CAP ? `${COUNT_CAP}+` : formatNumber(count));

/** Число у пункта — только когда работа ждёт (`NavItem`), ноль не рисуется. Точное
 *  число сверх потолка — на «Обзоре» и на экране раздела. */
function WorkCount({ count }: { count: number }) {
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

/**
 * Как пункт звучит — диктору и в подсказке свёрнутого меню: полным именем и с числом
 * ждущей работы. Полное имя — у пунктов с одной подписью на двоих (`sections.ts`, `name`):
 * «Письма» доноров и рекламодателей глазу различает группа, а диктору — только имя ссылки.
 */
function spoken(section: Section, count: number | undefined) {
  const name = section.name ?? section.title;
  const said = count !== undefined && count > 0 ? `${name} · ${capped(count)} ждут человека` : name;
  return { said, label: section.name === undefined ? undefined : said };
}

function NavItem({ section, count, to, active, folded }: ItemProps) {
  const Icon = section.icon;
  const waiting = count !== undefined && count > 0;
  const icon = <Icon size={20} stroke={1.6} aria-hidden />;
  const { said, label } = spoken(section, count);
  const link = (
    <NavLink
      label={folded ? <VisuallyHidden>{said}</VisuallyHidden> : section.title}
      aria-label={label}
      leftSection={
        folded && waiting ? (
          <Indicator size={8} color="yellow" offset={2}>
            {icon}
          </Indicator>
        ) : (
          icon
        )
      }
      // Правая часть — только под число: пустая всё равно держит свой зазор и отнимает
      // его у подписи, а `navWidth.ts` держит место лишь у пунктов, где число бывает, —
      // у «Продаж» при одном праве «Продажи» подпись резалась многоточием. `undefined`,
      // а не `null`: Mantine рисует часть при любом значении, кроме `undefined`.
      rightSection={!folded && waiting ? <WorkCount count={count} /> : undefined}
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
  /** Куда ведёт пункт: место, откуда из раздела ушли, или его адрес. */
  placeOf: (section: Section) => string;
  folded: boolean;
}

export function NavMenu({ sections, work, placeOf, folded }: Props) {
  const location = useLocation();
  const active = activeKey(sections, location.pathname, location.search);
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
                  key={sectionKey(section)}
                  section={section}
                  count={section.work === undefined ? undefined : work?.[section.work]}
                  to={placeOf(section)}
                  active={sectionKey(section) === active}
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
