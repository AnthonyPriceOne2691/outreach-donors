/**
 * Группа пунктов меню с подписью: «Доноры», «Рекламодатели», «Продажи», «Настройки» (`Shell`).
 *
 * **Продажи — своей группой** (просьба Anthony 11.10.2026): продажи — отдельный продукт,
 * и меню это показывает. Группа продаж — только с правом «Продажи»: без него в ней нечего
 * открыть (П2). **Доноры и рекламодатели — тоже группами** (23.10.2026, замечание
 * тест-группы): у каждой свои «Письма» и «Диалоги», и чьи они — говорит группа.
 *
 * Подпись — не пункт и не похожа на пункт: заглавными с разрядкой, цветом акцента
 * и с линией до края колонки (`.navGroupTitle`). Мелкая приглушённая подпись
 * сливалась с пунктами (замечание Anthony 10.10.2026). Группа размечена для
 * диктора (`role="group"` с подписью), иначе пятнадцать ссылок звучали бы подряд.
 */

import { Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import type { Permission } from '../api/types';

export type NavGroupKey = 'donors' | 'advertisers' | 'sales' | 'settings';

export interface NavGroupSpec {
  key: NavGroupKey;
  title: string;
  /** Право, без которого группы нет вовсе. */
  permission?: Permission;
}

export const NAV_GROUPS: NavGroupSpec[] = [
  { key: 'donors', title: 'Доноры' },
  { key: 'advertisers', title: 'Рекламодатели' },
  { key: 'sales', title: 'Продажи', permission: 'sales' },
  { key: 'settings', title: 'Настройки' },
];

interface Props {
  group: NavGroupSpec;
  /** Меню свёрнуто до значков: от подписи остаётся линия, название — диктору. */
  folded?: boolean;
  children: ReactNode;
}

export function NavGroup({ group, folded = false, children }: Props) {
  const id = `nav-group-${group.key}`;
  return (
    <Stack gap={4} role="group" aria-labelledby={id}>
      <Text id={id} px="sm" className="navGroupTitle" data-folded={folded || undefined}>
        <span className="navGroupName">{group.title}</span>
      </Text>
      {children}
    </Stack>
  );
}
