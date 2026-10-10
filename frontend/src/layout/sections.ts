/**
 * Разделы меню: адрес, название, группа, значок, право и число ждущей работы.
 *
 * Значок — у каждого пункта и в развёрнутом меню: свёрнутое меню — одни значки
 * (замечание Anthony 10.10.2026), и связь «значок — раздел» человек выучивает, пока
 * меню развёрнуто, а не угадывает потом.
 */

import {
  IconAdjustmentsHorizontal,
  IconAt,
  IconBan,
  IconBriefcase,
  IconFilterCheck,
  IconForms,
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

import type { Permission } from '../api/types';
import type { NavGroupKey } from './NavGroup';
import type { WorkSection } from './work';

export interface Section {
  path: string;
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

/** Две группы — решение Anthony 09.10.2026 (аудит экранов, второй круг): пятнадцать
 *  пунктов одним списком читались вперемешку — ежедневная работа рядом с тем, что
 *  настраивают раз в неделю. Порядок внутри групп — прежний: к нему привыкли. */
export const SECTIONS: Section[] = [
  { path: '/', title: 'Обзор', group: 'work', icon: IconLayoutDashboard },
  {
    path: '/run',
    title: 'Прогон',
    group: 'work',
    icon: IconPlayerPlay,
    permission: 'view',
    work: 'run',
  },
  { path: '/donors', title: 'Доноры', group: 'work', icon: IconWorldWww, permission: 'view' },
  { path: '/selection', title: 'Отбор', group: 'work', icon: IconFilterCheck, permission: 'view' },
  {
    path: '/forms',
    title: 'Формы',
    group: 'work',
    icon: IconForms,
    permission: 'view',
    work: 'forms',
  },
  {
    path: '/advertisers',
    title: 'Рекламодатели',
    group: 'work',
    icon: IconSpeakerphone,
    permission: 'view',
    work: 'advertisers',
  },
  // Своё право, а не `view`: раздел снимается с учётки поимённо (решение владельца 01.10).
  { path: '/sales', title: 'Продажи', group: 'work', icon: IconBriefcase, permission: 'sales' },
  { path: '/letters', title: 'Письма', group: 'work', icon: IconMail, permission: 'view' },
  {
    path: '/threads',
    title: 'Диалоги',
    group: 'work',
    icon: IconMessages,
    permission: 'view',
    work: 'threads',
    remember: true,
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
];

export const REMEMBERED = SECTIONS.filter((section) => section.remember).map(
  (section) => section.path,
);
