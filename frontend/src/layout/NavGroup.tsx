/**
 * Группа пунктов меню с подписью: «Работа» и «Настройки» (`Shell`).
 *
 * Подпись — не пункт и не похожа на пункт: заглавными с разрядкой, цветом акцента
 * и с линией до края колонки (`.navGroupTitle`). Мелкая приглушённая подпись
 * сливалась с пунктами (замечание Anthony 10.10.2026). Группа размечена для
 * диктора (`role="group"` с подписью), иначе пятнадцать ссылок звучали бы подряд.
 */

import { Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';

export type NavGroupKey = 'work' | 'settings';

export interface NavGroupSpec {
  key: NavGroupKey;
  title: string;
}

export const NAV_GROUPS: NavGroupSpec[] = [
  { key: 'work', title: 'Работа' },
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
