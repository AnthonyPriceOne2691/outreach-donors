/**
 * Значок «i» с подсказкой — пояснение, которому не место в строке.
 *
 * Один вид на весь фронт: до 28.09.2026 значок жил только у заголовка
 * вердикта на донорах; пороги получили такие же (замечание 28.09.2026:
 * «добавь значки подсказок, какие значения допустимы»).
 *
 * Подсказка открывается наведением, фокусом с клавиатуры и касанием: на
 * телефоне наведения нет, и подсказка только по наведению была бы с него
 * недоступна. Значок тихий, чтобы не спорить с тем, что поясняет; поверх
 * содержимого подсказка встаёт плотным стеклом (`.mantine-Tooltip-tooltip`).
 */

import { ActionIcon, Tooltip } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import type { ReactNode } from 'react';

interface Props {
  /** Имя значка для программы чтения с экрана — что именно он поясняет. */
  name: string;
  /** Текст подсказки. */
  children: ReactNode;
  /** Ширина подсказки для длинного пояснения — оно переносится в ней по
   *  словам. Без ширины подсказка в одну строку: граница «от 0 до 10 000 000»
   *  в узкой подсказке рвала число пополам. */
  width?: number;
}

export function InfoHint({ name, children, width }: Props) {
  return (
    <Tooltip
      multiline={width !== undefined}
      w={width}
      withArrow
      events={{ hover: true, focus: true, touch: true }}
      label={children}
    >
      <ActionIcon variant="subtle" size="sm" radius="xl" color="gray" aria-label={name}>
        <IconInfoCircle size={15} />
      </ActionIcon>
    </Tooltip>
  );
}
