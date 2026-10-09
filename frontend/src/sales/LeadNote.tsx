/**
 * Что очистка сказала о лиде — за значком «!», а не текстом в ячейке.
 *
 * Аудит экранов 09.10.2026: слова очистки («домен не принимает почту (нет MX и A): …»)
 * стояли в колонке без переноса строки и вылезали за ячейку в обе стороны. В ячейке
 * теперь значок причины и «!», слова целиком — по нажатию, как причина остановки
 * прогона (`runs/RunReason`): именно по нажатию, а не подсказкой по наведению — на
 * телефоне наведения нет.
 *
 * Цвет — по смыслу: у отклонённого — роза (отказ), у лида, оставшегося новым, потому
 * что проверка не выполнена, — янтарь (нужно внимание: следующая очистка повторит).
 * Поверх таблицы встаёт плотное стекло (`glassSolid`): сквозь прозрачное читались бы
 * строки под ним.
 */

import { ActionIcon, Popover, Text } from '@mantine/core';
import { IconAlertCircle } from '@tabler/icons-react';
import { useState } from 'react';

const TITLE = 'Что сказала очистка';

/** Место под «!» в строке без слов: соседние значки иначе встают лесенкой. */
export const NOTE_SLOT = 28;

export function LeadNote({ note, rejected }: { note: string; rejected: boolean }) {
  const [opened, setOpened] = useState(false);
  return (
    <Popover
      opened={opened}
      onChange={setOpened}
      position="bottom"
      withArrow
      width={340}
      radius="lg"
      shadow="md"
      // Фокус — внутрь, по закрытию — обратно на «!»: иначе Esc не закрывал поповер
      // (тот же урок, что у `RunReason`).
      trapFocus
      returnFocus
      middlewares={{ shift: { padding: 16 } }}
      transitionProps={{ transition: 'pop', duration: 180 }}
    >
      <Popover.Target>
        <ActionIcon
          variant="subtle"
          color={rejected ? 'red' : 'yellow'}
          radius="xl"
          aria-label={TITLE}
          style={{ flexShrink: 0 }}
          onClick={() => setOpened((was) => !was)}
        >
          <IconAlertCircle size={18} stroke={1.8} />
        </ActionIcon>
      </Popover.Target>
      {/* Не шире окна за вычетом полей: на узком телефоне текст переносится. */}
      <Popover.Dropdown className="glassSolid" maw="calc(100vw - 32px)">
        <Text size="sm" fw={600} mb={4}>
          {TITLE}
        </Text>
        <Text size="sm" className="leadNote" style={{ overflowWrap: 'break-word' }}>
          {note}
        </Text>
      </Popover.Dropdown>
    </Popover>
  );
}
