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

import { SegmentedControl, Tooltip, useMantineColorScheme } from '@mantine/core';
import { IconDeviceLaptop, IconMoon, IconSun } from '@tabler/icons-react';

const SIZE = 16;

export function ThemeToggle() {
  // `keepTransitions` — не украшение. Mantine на время переключения
  // вставляет в документ `*, *::before, *::after { transition: none }`
  // на десять миллисекунд: так он борется с миганием у тех, у кого
  // переходов нет вовсе. У нас полотно переливается двумя слоями, и это
  // правило убивало ровно то, ради чего слои и заведены, — смена темы
  // выглядела мгновенной, хотя всё для перехода было на месте.
  const { colorScheme, setColorScheme } = useMantineColorScheme({ keepTransitions: true });

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
