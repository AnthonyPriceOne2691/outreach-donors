/**
 * Подпись поля — словом перед ним, в одну строку: значение «доноры» или «по 50» без неё
 * читалось бы загадкой. Для полей над таблицей — разреза всего списка (порядок, решение, цена
 * без своей колонки); фильтр колонки подписан самой колонкой и живёт под её заголовком.
 */

import { Group, Text } from '@mantine/core';
import type { ReactNode } from 'react';

export function Labeled({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <Group gap={6} wrap="nowrap">
      <Text size="sm" c="dimmed" component="span">
        {label}
      </Text>
      {children}
    </Group>
  );
}
