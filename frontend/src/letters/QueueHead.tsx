/**
 * Шапка очереди на «Письмах»: сколько в очереди, сколько вне коридора — и кнопка пачки
 * справа, над самой очередью.
 *
 * Аудит экранов 09.10.2026: «Отправить очередь · N» висела между формой сборки и
 * очередью, отдельно от обеих, и было непонятно, к чему она относится; при пустой
 * очереди это была серая пилюля в пустоте. Числа «В очереди» и «Вне коридора» стояли
 * плитками по ~105 px наверху экрана, а действуют по ним здесь — у кнопки и у списка.
 */

import { Badge, Card, Group, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';

import type { Corridor, QueuedLetter } from '../api/types';
import { formatNumber } from '../format';
import { corridorText } from './letterText';

interface Props {
  /** Вся очередь этапа — тем же счётом, что кнопка пачки, а не длина списка: список
   *  экрана обрезан потолком. */
  total: number;
  letters: QueuedLetter[];
  corridor: Corridor;
  /** Кнопка пачки с её строкой итога (`SendQueue`) — справа. */
  send: ReactNode;
}

export function QueueHead({ total, letters, corridor, send }: Props) {
  const offCorridor = letters.filter((letter) => letter.verdict !== null).length;
  return (
    <Card className="glassPanel" px="xl" py="md">
      <Group justify="space-between" align="center" gap="sm">
        <Group gap="sm" align="center">
          <Title order={4}>Очередь</Title>
          <Text size="sm" c="var(--ink)">
            {formatNumber(total)}
          </Text>
          {offCorridor > 0 && (
            <Badge variant="light" color="yellow">
              вне коридора {formatNumber(offCorridor)} · коридор {corridorText(corridor)}
            </Badge>
          )}
        </Group>
        {send}
      </Group>
    </Card>
  );
}
