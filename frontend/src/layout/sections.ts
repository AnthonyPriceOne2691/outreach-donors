/**
 * Разделы меню: адрес, название, группа, значок, право и число ждущей работы.
 *
 * Значок — у каждого пункта и в развёрнутом меню: свёрнутое меню — одни значки
 * (замечание Anthony 10.10.2026), и связь «значок — раздел» человек выучивает, пока
 * меню развёрнуто, а не угадывает потом.
 *
 * **Продажи — своей группой** (просьба Anthony 11.10.2026). В неё же — вход в часть
 * продаж общих экранов: «Диалоги продаж», «Агент продаж», «Домены продаж» — те же
 * экраны, суженные адресом (`?stage=sales`, `api/stages.ts`). Два пункта одного пути
 * различаются частью: она — в ключе пункта, в его подсветке и в памяти места.
 */

import {
  IconAdjustmentsHorizontal,
  IconAt,
  IconBan,
  IconBriefcase,
  IconFilterCheck,
  IconForms,
  IconHistory,
  IconLayoutDashboard,
  IconMail,
  IconMessages,
  IconPlayerPlay,
  IconReceipt2,
  IconRobot,
  IconSpeakerphone,
  IconUsers,
  IconWorldWww,
} from '@tabler/icons-react';
import type { Icon } from '@tabler/icons-react';

import { PART_PARAM } from '../api/stages';
import type { Part } from '../api/stages';
import type { Permission } from '../api/types';
import type { NavGroupKey } from './NavGroup';
import type { WorkSection } from './work';

export interface Section {
  path: string;
  /** Часть общего экрана: `sales` — только продажи (`?stage=sales`). Пункт без части
   *  на том же пути — экран без неё: «Диалоги» — доноры и рекламодатели. */
  part?: Part;
  title: string;
  group: NavGroupKey;
  icon: Icon;
  permission?: Permission;
  /** Какое число ждущей работы стоит у пункта. */
  work?: WorkSection;
  /** Пункт ведёт туда, откуда из раздела ушли (`sectionPlace.ts`): список и запись —
   *  на одном экране, и возврат в раздел не должен закрывать открытую запись. */
  remember?: true;
}

/** Три группы — просьба Anthony 11.10.2026; до неё — «Работа» и «Настройки» (решение
 *  09.10.2026). Порядок внутри групп — прежний: к нему привыкли. В группе продаж — сам
 *  раздел, затем части общих экранов в том порядке, в каком эти экраны стоят в меню. */
export const SECTIONS: Section[] = [
  { path: '/', title: 'Обзор', group: 'mail', icon: IconLayoutDashboard },
  {
    path: '/run',
    title: 'Прогон',
    group: 'mail',
    icon: IconPlayerPlay,
    permission: 'view',
    work: 'run',
  },
  { path: '/donors', title: 'Доноры', group: 'mail', icon: IconWorldWww, permission: 'view' },
  { path: '/selection', title: 'Отбор', group: 'mail', icon: IconFilterCheck, permission: 'view' },
  {
    path: '/forms',
    title: 'Формы',
    group: 'mail',
    icon: IconForms,
    permission: 'view',
    work: 'forms',
  },
  {
    path: '/advertisers',
    title: 'Рекламодатели',
    group: 'mail',
    icon: IconSpeakerphone,
    permission: 'view',
    work: 'advertisers',
  },
  { path: '/letters', title: 'Письма', group: 'mail', icon: IconMail, permission: 'view' },
  {
    path: '/threads',
    title: 'Диалоги',
    group: 'mail',
    icon: IconMessages,
    permission: 'view',
    work: 'threads',
    remember: true,
  },
  // Своё право, а не `view`: раздел снимается с учётки поимённо (решение владельца 01.10).
  { path: '/sales', title: 'Продажи', group: 'sales', icon: IconBriefcase, permission: 'sales' },
  {
    path: '/threads',
    part: 'sales',
    title: 'Диалоги продаж',
    group: 'sales',
    icon: IconMessages,
    permission: 'view',
    work: 'sales_threads',
    remember: true,
  },
  {
    path: '/agent',
    part: 'sales',
    title: 'Агент продаж',
    group: 'sales',
    icon: IconRobot,
    permission: 'view',
  },
  {
    path: '/senders',
    part: 'sales',
    title: 'Домены продаж',
    group: 'sales',
    icon: IconAt,
    permission: 'senders',
  },
  {
    path: '/suppressions',
    title: 'Стоп-лист',
    group: 'settings',
    icon: IconBan,
    permission: 'view',
  },
  {
    path: '/settings',
    title: 'Пороги',
    group: 'settings',
    icon: IconAdjustmentsHorizontal,
    permission: 'view',
  },
  {
    path: '/agent',
    title: 'Агент переписки',
    group: 'settings',
    icon: IconRobot,
    permission: 'view',
  },
  { path: '/usage', title: 'Расход', group: 'settings', icon: IconReceipt2, permission: 'view' },
  {
    path: '/senders',
    title: 'Домены рассылки',
    group: 'settings',
    icon: IconAt,
    permission: 'senders',
  },
  { path: '/users', title: 'Учётки', group: 'settings', icon: IconUsers, permission: 'users' },
  // Рядом с «Учётками» и под тем же правом: в журнале входы, права и пароли (11.10.2026).
  {
    path: '/journal',
    title: 'Журнал действий',
    group: 'settings',
    icon: IconHistory,
    permission: 'users',
  },
];

/** Ключ пункта — его адрес: путь, у части — с `?stage=…`. Он же — адрес пункта, пока
 *  память места пуста, и ключ этой памяти. */
export function sectionKey(section: Pick<Section, 'path' | 'part'>): string {
  return section.part === undefined
    ? section.path
    : `${section.path}?${PART_PARAM}=${section.part}`;
}

/** Адрес внутри раздела: «Прогон» (`/run`) — и отчёт `/runs/27`, и его рассмотрение. */
function inside(path: string, pathname: string): boolean {
  return path === '/' ? pathname === '/' : pathname.startsWith(path);
}

/**
 * Пункт, которому принадлежит адрес, — ключом. Самый длинный подходящий путь; из пунктов
 * одного пути — тот, чья часть названа в адресе (`?stage=sales`), а без неё — пункт без
 * части. Части, пункта которой у человека нет (нет права), нет и в подсветке: адрес
 * достаётся пункту без части, как и экран (`readThreadFilters`).
 */
export function activeKey(
  sections: readonly Section[],
  pathname: string,
  search: string,
): string | null {
  const matching = sections.filter((section) => inside(section.path, pathname));
  const longest = Math.max(0, ...matching.map((section) => section.path.length));
  const same = matching.filter((section) => section.path.length === longest);
  const part = new URLSearchParams(search).get(PART_PARAM);
  const chosen =
    same.find((section) => section.part !== undefined && section.part === part) ??
    same.find((section) => section.part === undefined);
  return chosen === undefined ? null : sectionKey(chosen);
}
