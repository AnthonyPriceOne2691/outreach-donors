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
 * **Очередь длиннее пачки — окно так и говорит**: «в очереди N; одна пачка
 * берёт до M, остальное — следующей», и кнопка окна называет то, что уйдёт
 * этим нажатием (M), а не всю очередь. Потолок M называет сервер (`batch_max`):
 * копия числа здесь разошлась бы с пачкой при первой его правке.
 *
 * **Итог — словами под кнопкой**, из отчёта задачи: сколько ушло, что не ушло
 * и почему, сколько осталось. Номер задачи переживает перезагрузку страницы. Номер у
 * пачки вкладки постоянный — пачка одна за раз (проверка QA 10.10.2026), — и строка
 * итога после нажатия спрашивает задачу заново: под прежним номером уже новая пачка.
 *
 * **Пачка — одной аудитории.** У Этапа 2 две вкладки: рекламодатели по найденной
 * ссылке и бизнесы ниши. Кнопка вкладки отправляет только её письма, число на ней —
 * её очередь, и последняя пачка у каждой своя.
 *
 * **Закрытая почтой кнопка говорит почему — строкой под собой** (проверка QA 10.10.2026):
 * причина была только в плашке наверху экрана, а у кнопки и по наведению — ничего.
 * Строкой, а не подсказкой: на телефоне наведения нет (`docs/UI_RULES.md`).
 */

import { Alert, Button, Group, Modal, Stack, Text } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useId, useState } from 'react';

import { refusalOf } from '../api/client';
import { sendQueue } from '../api/letters';
import type { LetterAudience } from '../api/letters';
import type { Stage } from '../api/stages';
import { formatNumber, plural } from '../format';
import { JobLine, jobRestarted } from '../jobs/JobLine';
import { remember, remembered } from '../storage';
import { notify } from '../notices';

/** Где помнится последняя пачка вкладки. «По ссылке» — прежний ключ этапа. */
const keyOf = (stage: Stage, audience: LetterAudience) =>
  audience === 'links'
    ? `letters:last-send-queue:${stage}`
    : `letters:last-send-queue:${stage}:${audience}`;

/** Почему закрыта пачка, когда экран не назвал своих слов: что именно не так, сказано
 *  выше — плашкой экрана писем или блоком подключения продаж. */
const BLOCKED_WHY = 'Не нажимается, пока почта не подключена — что не так, сказано выше';

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
  /** Этап очереди — любой, и продажи тоже: их письма почта отправляет через мост продаж.
   *  Продажи не подключены — сервер отказывает словами (409), а отключились по ходу
   *  пачки — причина в её итоге. */
  stage: Stage;
  /** Аудитория Этапа 2; нет — по найденной ссылке, как до аудиторий. */
  audience?: LetterAudience;
  count: number;
  /** Сколько писем берёт одна пачка — число сервера из ответа, который дал `count`.
   *  Нет — окно говорит без потолка: он неизвестен. */
  batchMax?: number;
  /** Почта не подключена или не заполнены обязательные поля письма. */
  blocked: boolean;
  /** Почему закрыта — словами экрана; нет — общими (`BLOCKED_WHY`). */
  blockedWhy?: string;
  onFinished: () => void;
}

/** Кнопка пачки и, у закрытой почтой, — почему: строкой под ней. Строка — и описание
 *  кнопки: её слышит экранный диктор. */
function BatchButton({
  count,
  why,
  onOpen,
}: {
  count: number;
  why: string | null;
  onOpen: () => void;
}) {
  const reasonId = useId();
  return (
    <>
      <Group>
        <Button
          color="lagoon"
          className="press"
          disabled={count === 0 || why !== null}
          aria-describedby={why === null ? undefined : reasonId}
          onClick={onOpen}
        >
          Отправить очередь · {formatNumber(count)}
        </Button>
      </Group>
      {why === null ? null : (
        <Text id={reasonId} size="sm" c="dimmed">
          {why}
        </Text>
      )}
    </>
  );
}

export function SendQueue({
  stage,
  audience = 'links',
  count,
  batchMax,
  blocked,
  blockedWhy = BLOCKED_WHY,
  onFinished,
}: Props) {
  const client = useQueryClient();
  const [opened, setOpened] = useState(false);
  const [jobId, setJobId] = useState<string | null>(() => remembered(keyOf(stage, audience)));
  useEffect(() => setJobId(remembered(keyOf(stage, audience))), [stage, audience]);

  const start = useMutation({
    mutationFn: () => sendQueue(stage, audience),
    onSuccess: (queued) => {
      setOpened(false);
      setJobId(queued.job_id);
      remember(keyOf(stage, audience), queued.job_id);
      void jobRestarted(client, queued.job_id);
      notify({
        message: `Пачка ушла в очередь задач: писем ${formatNumber(queued.queued)}`,
        color: 'green',
      });
    },
  });
  // Окно открывается и закрывается без отказа прежнего нажатия. Пока запрос идёт, окно
  // не закрывается: уведомления об отказе нет, и отказ после закрытия не увидели бы нигде.
  const toggle = (open: boolean) => {
    if (start.isPending) return;
    start.reset();
    setOpened(open);
  };

  // Пачка берёт не больше потолка сервера: длинная очередь уходит несколькими нажатиями.
  const cap = batchMax !== undefined && count > batchMax ? batchMax : null;
  const letters = `${formatNumber(count)} ${plural(count, 'письмо', 'письма', 'писем')}`;
  const queued =
    cap === null
      ? `В очереди ${letters}: каждое`
      : `В очереди ${letters}; одна пачка берёт до ${formatNumber(cap)}, остальное — следующей. Каждое`;
  return (
    <Stack gap={6}>
      <BatchButton count={count} why={blocked ? blockedWhy : null} onOpen={() => toggle(true)} />
      {jobId !== null ? (
        <JobLine jobId={jobId} onFinished={onFinished} describe={batchLine} />
      ) : null}
      <Modal opened={opened} onClose={() => toggle(false)} title="Отправить всю очередь?">
        <Stack gap="sm">
          <Text size="sm">
            {queued} уйдёт тем же путём, что по одному, — стоп-листы, решение по адресату,
            предохранитель. Сколько уйдёт сегодня, решает дневной лимит ящиков; остальное останется
            в очереди до завтра.
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
            <Button variant="default" disabled={start.isPending} onClick={() => toggle(false)}>
              Отмена
            </Button>
            <Button color="lagoon" loading={start.isPending} onClick={() => start.mutate()}>
              Отправить {formatNumber(cap ?? count)}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
