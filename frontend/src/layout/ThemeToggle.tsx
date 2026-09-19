/**
 * Переключатель темы.
 *
 * Три положения, а не два: «как в системе» — это не то же самое, что
 * светлая. Человек, у которого система переключается по времени суток,
 * выбрав «светлую», получил бы её и ночью.
 *
 * Выбор запоминает Mantine (`localStorage`), и он же первым кадром ставит
 * атрибут на `<html>`, по которому красится полотно и стекло.
 */

import { SegmentedControl, Tooltip } from '@mantine/core';
import { useMantineColorScheme } from '@mantine/core';
import { IconDeviceLaptop, IconMoon, IconSun } from '@tabler/icons-react';

const SIZE = 16;

export function ThemeToggle() {
  const { colorScheme, setColorScheme } = useMantineColorScheme();

  return (
    <SegmentedControl
      value={colorScheme}
      onChange={(value) => setColorScheme(value as 'light' | 'dark' | 'auto')}
      size="xs"
      radius="xl"
      fullWidth
      data={[
        {
          value: 'light',
          label: (
            <Tooltip label="Светлая тема" withArrow>
              <IconSun size={SIZE} aria-label="Светлая тема" />
            </Tooltip>
          ),
        },
        {
          value: 'auto',
          label: (
            <Tooltip label="Как в системе" withArrow>
              <IconDeviceLaptop size={SIZE} aria-label="Как в системе" />
            </Tooltip>
          ),
        },
        {
          value: 'dark',
          label: (
            <Tooltip label="Тёмная тема" withArrow>
              <IconMoon size={SIZE} aria-label="Тёмная тема" />
            </Tooltip>
          ),
        },
      ]}
    />
  );
}
