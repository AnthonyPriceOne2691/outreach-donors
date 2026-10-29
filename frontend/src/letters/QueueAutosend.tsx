/**
 * Очередь, которая уходит сама, — строкой под кнопкой пачки (замечание тест-группы 29.10.2026:
 * «автоматическая ежедневная отправка очереди, если её запустил»).
 *
 * Идёт — «Очередь уходит сама с … · включил … · ушло N», под ней — чем кончился последний
 * проход, словами сервера (ящики на паузе до …, писать некому — продолжит завтра, почта
 * отказала …), и кнопка «Остановить»: письма остаются в очереди и дальше уходят только кнопкой.
 * Рубильник сервиса выключен — так и сказано: проходы стоят, хотя запуск не остановлен.
 * Остановлена — одной строкой: когда, кем или почему («очередь кончилась»). Не включалась ни
 * разу — строки нет.
 *
 * Отказ остановки — строкой здесь же, как у пачки: уведомление внизу экрана не видели.
 */

import { Button, Group, Stack, Text } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';

import type { QueueAutosend } from '../api/autosend';
import { refusalOf } from '../api/client';
import { stopAutosend } from '../api/letters';
import type { LetterAudience } from '../api/letters';
import type { Stage } from '../api/stages';
import { formatDateTime, formatNumber } from '../format';

interface Props {
  stage: Stage;
  audience?: LetterAudience;
  autosend: QueueAutosend;
  /** Право «отправлять письма»: без него кнопки «Остановить» нет, строка — есть. */
  canStop: boolean;
  onChanged: () => void;
}

/** «Очередь уходит сама с 29.10, 10:05 · включил … · ушло 12». */
export function runningLine(autosend: QueueAutosend): string {
  const who = autosend.started_by ? ` · включил ${autosend.started_by}` : '';
  const sent = formatNumber(autosend.sent);
  return `Очередь уходит сама с ${formatDateTime(autosend.since)}${who} · ушло ${sent}`;
}

/** «Сама больше не уходит с 29.10, 18:00: остановлено кнопкой … (кем)». */
export function stoppedLine(autosend: QueueAutosend): string {
  const who = autosend.stopped_by ? ` (${autosend.stopped_by})` : '';
  const why = autosend.stop_reason ? `: ${autosend.stop_reason}` : '';
  return `Сама больше не уходит с ${formatDateTime(autosend.stopped_at)}${why}${who}`;
}

export function QueueAutosendLine({
  stage,
  audience = 'links',
  autosend,
  canStop,
  onChanged,
}: Props) {
  const stop = useMutation({
    mutationFn: () => stopAutosend(stage, audience),
    onSuccess: onChanged,
  });
  if (!autosend.running) {
    return autosend.stopped_at ? (
      <Text size="sm" c="dimmed">
        {stoppedLine(autosend)}
      </Text>
    ) : null;
  }
  const state = autosend.off ?? autosend.state;
  return (
    <Stack gap={4}>
      <Group gap="sm" wrap="nowrap" justify="space-between">
        <Text size="sm">{runningLine(autosend)}</Text>
        {canStop ? (
          <Button
            size="xs"
            variant="default"
            loading={stop.isPending}
            onClick={() => stop.mutate()}
          >
            Остановить
          </Button>
        ) : null}
      </Group>
      {state ? (
        <Text size="sm" c="dimmed">
          {autosend.passed_at && !autosend.off ? `${formatDateTime(autosend.passed_at)}: ` : ''}
          {state}
        </Text>
      ) : null}
      {stop.isError ? (
        <Text size="sm" c="red">
          Не остановили: {refusalOf(stop.error)}
        </Text>
      ) : null}
    </Stack>
  );
}
