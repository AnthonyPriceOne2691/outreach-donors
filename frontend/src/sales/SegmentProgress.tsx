/**
 * Ход прогона под шапкой отчёта: этап и «X из Y», сколько идёт проход и когда прогон
 * отмечался; где встал остановленный и почему — причиной строки или, у строки «идёт» без
 * задачи, словами очереди; «Задачу некому взять»; очередь не ответила; сбой выдачи и что
 * будет со следующим проходом — после причины, а не перед ней.
 *
 * Слова и правила — `segmentStage.ts`; жив ли прогон, решает сервер по очереди задач.
 */

import { Alert, Stack, Text } from '@mantine/core';

import type { SegmentRunRow } from './segmentApi';
import { isActive, pulse, serpMode, stageLine, stoppedAt } from './segmentStage';

/** Ход идущего прогона одной строкой: этап, режим выдачи, сколько идёт и когда отмечался. */
function GoingLine({ run }: { run: SegmentRunRow }) {
  const beat = pulse(run);
  const mode = serpMode(run);
  return (
    <Text size="sm" role="status" aria-label="Ход прогона">
      {stageLine(run)}
      {mode === null ? '' : ` (${mode})`}
      {beat !== null &&
        (beat.quiet ? (
          <Text span size="sm" fw={500}>
            {' '}
            · {beat.words}
          </Text>
        ) : (
          <Text span size="sm" c="dimmed">
            {' '}
            · {beat.words}
          </Text>
        ))}
    </Text>
  );
}

/** Почему прогон остановлен — словами сервера: причина в строке прогона, а у строки «идёт»
 *  без задачи — ответ очереди. Пока продолжение идёт, прежней причины нет. */
function Stopped({ run }: { run: SegmentRunRow }) {
  const words = run.watch?.stalled ?? (isActive(run) ? null : run.error);
  if (words === null) return null;
  return (
    <Alert color="yellow" title="Прогон остановлен">
      {words}
    </Alert>
  );
}

/** Задача в очереди, а воркеров нет — что происходит и кто это чинит, без команд консоли. */
function NoWorker({ run }: { run: SegmentRunRow }) {
  if (!isActive(run) || run.watch?.workers !== 0) return null;
  return (
    <Alert color="yellow" title="Задачу некому взять">
      Прогон стоит в очереди: служба долгих задач продаж не запущена. Запустить её может
      администратор сервиса — сообщите ему, и прогон пойдёт сам, с места, где стоит.
    </Alert>
  );
}

/** Очередь не ответила: «не знаю» — не «мертва». */
function Silent({ run }: { run: SegmentRunRow }) {
  if (!isActive(run) || run.watch?.silent !== true) return null;
  return (
    <Text size="sm" c="dimmed">
      Очередь задач не ответила — жив ли прогон, сейчас не узнать; экран спросит снова.
    </Text>
  );
}

/** Сбой этапа и что будет со следующим проходом: «выдача упала после оплаты — купит заново». */
function Note({ words }: { words: string | null }) {
  if (words === null) return null;
  return (
    <Alert color="yellow" title="Сбой выдачи">
      {words}
    </Alert>
  );
}

export function SegmentProgress({ run }: { run: SegmentRunRow }) {
  const where = stoppedAt(run);
  return (
    <Stack gap={6}>
      {isActive(run) && <GoingLine run={run} />}
      {where !== null && (
        <Text size="sm" c="dimmed">
          {where}
        </Text>
      )}
      <Stopped run={run} />
      <NoWorker run={run} />
      <Silent run={run} />
      <Note words={run.progress?.note ?? null} />
    </Stack>
  );
}
