/**
 * Почему прогон остановлен — за значком «!», а не текстом в ячейке.
 *
 * Замечание 25.09.2026: причина в три-пять строк раздувала колонку
 * состояния, и строки истории вставали разной высоты. В ячейке теперь
 * только значок, а текст целиком — по нажатию. Именно по нажатию,
 * а не подсказкой по наведению: на телефоне наведения нет, и причина
 * с него была бы недоступна вовсе.
 *
 * Поверх содержимого встаёт плотное стекло (`glassSolid`): сквозь
 * прозрачное читались бы строки таблицы под ним.
 */

import { ActionIcon, Popover, Text } from '@mantine/core';
import { IconAlertCircle } from '@tabler/icons-react';
import { useState } from 'react';

import type { RunStatus } from '../api/types';

interface Props {
  reason: string;
  status: RunStatus;
}

export function RunReason({ reason, status }: Props) {
  const [opened, setOpened] = useState(false);
  // Цвет — по смыслу. Остановленный сам не продолжится, ждать нечего:
  // это отказ, роза. Прогон, который прерывался, но продолжен или будет
  // продолжен, — «нужно внимание», янтарь.
  const stopped = status === 'stopped';
  const title = stopped ? 'Почему остановлен' : 'Что случилось';

  return (
    <Popover
      opened={opened}
      onChange={setOpened}
      position="bottom"
      withArrow
      width={340}
      radius="lg"
      shadow="md"
      // Фокус уходит внутрь, а по закрытию — обратно на «!». Без этого Esc
      // не закрывал поповер: Mantine ловит его на самом поповере, а фокус
      // оставался на кнопке (проверено клавиатурой в браузере).
      trapFocus
      returnFocus
      // От края окна — те же 16 пикселей, что у страницы на телефоне:
      // по умолчанию поповер прижимается к краю на пять.
      middlewares={{ shift: { padding: 16 } }}
      transitionProps={{ transition: 'pop', duration: 180 }}
    >
      <Popover.Target>
        <ActionIcon
          variant="subtle"
          color={stopped ? 'red' : 'yellow'}
          radius="xl"
          aria-label={title}
          style={{ flexShrink: 0 }}
          onClick={() => setOpened((was) => !was)}
        >
          <IconAlertCircle size={18} stroke={1.8} />
        </ActionIcon>
      </Popover.Target>
      {/* Ширина не больше окна за вычетом полей: на самом узком телефоне
          текст переносится, а не уезжает за край. */}
      <Popover.Dropdown className="glassSolid" maw="calc(100vw - 32px)">
        <Text size="sm" fw={600} mb={4}>
          {title}
        </Text>
        {/* Переносы в тексте сохраняются: отказ настроек перечисляет,
            чего не хватает, по строке на пункт. */}
        <Text
          size="sm"
          data-run-reason
          style={{ whiteSpace: 'pre-line', overflowWrap: 'break-word' }}
        >
          {reason}
        </Text>
      </Popover.Dropdown>
    </Popover>
  );
}
