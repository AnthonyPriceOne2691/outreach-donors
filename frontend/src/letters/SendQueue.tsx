/**
 * «Отправить очередь · N» — вся очередь этапа пачкой (слово Anthony 06.10.2026).
 *
 * До этого кнопки не было намеренно: каждое письмо читал человек
 * (`docs/WEB_LAYER.md`). Для запуска выбрана пачка — «вся очередь». Письма
 * уходят тем же путём, что по одному (`letters/batch.py` на сервере):
 * стоп-листы, решение по адресату, предохранитель, дневной лимит ящиков,
 * и в журнал каждое — с тем, кто нажал.
 *
 * **Подтверждение — с числом и с тем, что ограничит отправку.** Пачку не
 * отзовёшь: окно говорит, сколько писем и почему уйдут не все, до нажатия.
 *
 * **Отказ сервера — в том же окне, где нажали**, и только там. Окно после
 * отказа остаётся открытым с прежней кнопкой: отказ уведомлением внизу экрана
 * человек не видел и жал «Отправить» снова (ревью стыков). Второго места для
 * отказа нет — два одинаковых текста на экране читались бы двумя отказами.
 * Ошибка прежнего нажатия гаснет, когда окно закрывают и открывают снова.
 *
 * **Итог — словами под кнопкой**, из отчёта задачи: сколько ушло, что не ушло
 * и почему, сколько осталось. Номер задачи переживает перезагрузку страницы.
 */

import { Alert, Button, Group, Modal, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { refusalOf } from '../api/client';
import { sendQueue } from '../api/letters';
import type { Stage } from '../api/stages';
import { formatNumber, plural } from '../format';
import { JobLine } from '../jobs/JobLine';
import { remember, remembered } from '../storage';

const keyOf = (stage: Stage) => `letters:last-send-queue:${stage}`;

/** Итог пачки одной строкой: что ушло, что нет и почему, что осталось. */
export function batchLine(report: Record<string, unknown>): string {
  const refused = (report.refused ?? {}) as Record<string, number>;
  const parts = [`Ушло ${formatNumber(Number(report.sent ?? 0))}`];
  for (const [why, count] of Object.entries(refused)) {
    parts.push(`${why} — ${formatNumber(count)}`);
  }
  parts.push(`осталось в очереди ${formatNumber(Number(report.left ?? 0))}`);
  const stopped = typeof report.stopped === 'string' ? ` Остановлено: ${report.stopped}` : '';
  return `${parts.join(', ')}.${stopped}`;
}

interface Props {
  /** Этап очереди — любой, и продажи тоже: сервер отказывает словами, если почта
   *  этап ещё не ведёт (409), и называет причину в итоге пачки. */
  stage: Stage;
  count: number;
  /** Почта не подключена или не заполнены обязательные поля письма. */
  blocked: boolean;
  onFinished: () => void;
}

export function SendQueue({ stage, count, blocked, onFinished }: Props) {
  const [opened, setOpened] = useState(false);
  const [jobId, setJobId] = useState<string | null>(() => remembered(keyOf(stage)));
  useEffect(() => setJobId(remembered(keyOf(stage))), [stage]);

  const start = useMutation({
    mutationFn: () => sendQueue(stage),
    onSuccess: (queued) => {
      setOpened(false);
      setJobId(queued.job_id);
      remember(keyOf(stage), queued.job_id);
      notifications.show({
        message: `Пачка ушла в очередь задач: писем ${formatNumber(queued.queued)}`,
        color: 'green',
      });
    },
  });
  // Окно открывается и закрывается без отказа прежнего нажатия.
  const toggle = (open: boolean) => {
    start.reset();
    setOpened(open);
  };

  const letters = plural(count, 'письмо', 'письма', 'писем');
  return (
    <Stack gap={6}>
      <Group>
        <Button
          color="lagoon"
          className="press"
          disabled={count === 0 || blocked}
          onClick={() => toggle(true)}
        >
          Отправить очередь · {formatNumber(count)}
        </Button>
      </Group>
      {jobId !== null ? (
        <JobLine jobId={jobId} onFinished={onFinished} describe={batchLine} />
      ) : null}
      <Modal opened={opened} onClose={() => toggle(false)} title="Отправить всю очередь?">
        <Stack gap="sm">
          <Text size="sm">
            В очереди {formatNumber(count)} {letters}: каждое уйдёт тем же путём, что по одному, —
            стоп-листы, решение по адресату, предохранитель. Сколько уйдёт сегодня, решает дневной
            лимит ящиков; остальное останется в очереди до завтра.
          </Text>
          <Text size="sm" c="dimmed">
            Отправленное письмо не отзывается.
          </Text>
          {start.isError ? (
            <Alert color="red" title="Не отправили">
              {refusalOf(start.error)}
            </Alert>
          ) : null}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => toggle(false)}>
              Отмена
            </Button>
            <Button color="lagoon" loading={start.isPending} onClick={() => start.mutate()}>
              Отправить {formatNumber(count)}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
