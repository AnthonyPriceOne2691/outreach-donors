/**
 * Пустая очередь на «Письмах» — ступенями воронки: где кончились адресаты.
 *
 * Аудит экранов 09.10.2026: причина стояла абзацем «Подходящих доноров 12, из них
 * с адресом 0, и ещё не писали 0. Если последнее число ноль — написаны все; если ноль
 * второе — …», и его приходилось решать, как задачу. Цепочка «подходящих → с адресом →
 * ещё не писали» с отмеченной ступенью отвечает взглядом, совет — строкой под ней.
 * Ступени после отмеченной — бледнее: воронка накопительная, и их нули — следствие.
 */

import { Badge, Card, Group, Stack, Text } from '@mantine/core';

import { formatNumber } from '../format';
import type { LetterTarget } from './targets';
import { funnelNotes, funnelSteps, stopOf } from './targets';

interface Props {
  funnel: Record<string, number>;
  target: LetterTarget;
  /** Показана воронка прежнего этапа, пока идёт новая. */
  stale: boolean;
}

export function EmptyQueue({ funnel, target, stale }: Props) {
  const steps = funnelSteps(funnel, target);
  const stop = stopOf(steps);
  const stopAt = stop === null ? steps.length : steps.indexOf(stop);
  return (
    <Card className="glass staleRows" p="xl" data-stale={stale || undefined}>
      <Stack gap="sm">
        <Text fw={500}>Очередь пуста</Text>
        <Group gap={6} role="list" aria-label="Где кончились адресаты">
          {/* Стрелка переносится вместе со ступенью за ней: на телефоне строка
              начинается с «→», а не кончается им. */}
          {steps.map((one, index) => (
            <Group key={one.step} gap={6} wrap="nowrap">
              {index > 0 && (
                <Text size="sm" c="dimmed" aria-hidden>
                  →
                </Text>
              )}
              <Badge
                role="listitem"
                variant={index > stopAt ? 'outline' : 'light'}
                color={index === stopAt ? 'yellow' : 'gray'}
              >
                {one.step} {formatNumber(one.count)}
              </Badge>
            </Group>
          ))}
        </Group>
        <Text size="sm">
          {stop === null
            ? 'Адресаты есть — очередь ещё не собрана.'
            : `Кончились на ступени «${stop.step}»: ${stop.advice}.`}
        </Text>
        {funnelNotes(funnel, target).map((note) => (
          <Text key={note} size="sm" c="dimmed">
            {note}
          </Text>
        ))}
      </Stack>
    </Card>
  );
}
