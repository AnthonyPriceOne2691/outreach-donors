/**
 * «Переписать шаблонные · N» — письма очереди, оставшиеся шаблоном из-за отказа модели.
 *
 * 23.11.2026 у модели кончились деньги посреди сборки, и письма встали в очередь с зонами
 * шаблона (отличие 0%). Кнопка переписывает их заново тем же путём, что сборка (сервер:
 * `letters/rewrite_queue.py`): поправленные руками и ушедшие письма она не трогает, а если
 * модель снова откажет, письмо останется как было.
 *
 * **Видна, только когда такие письма есть** — число называет сервер (`templated`). Нажатие
 * спрашивает подтверждения маленьким окном у кнопки (`ConfirmPopover`): что сделает, сколько
 * писем и что это вызовы модели. Ход и итог — строкой задачи под кнопкой, как у сборки; номер
 * задачи переживает перезагрузку. Пока очередь уходит сама, кнопка не нажимается и говорит
 * почему — строкой под собой: шаблонные письма ушли бы раньше, чем их перепишет модель.
 */

import { Button, Group, Stack, Text } from '@mantine/core';
import { useId } from 'react';

import { rewriteTemplated } from '../api/letters';
import type { LetterAudience } from '../api/letters';
import type { LetterStage } from '../api/types';
import { ConfirmPopover } from '../components/ConfirmPopover';
import { formatNumber, plural } from '../format';
import { JobLine } from '../jobs/JobLine';
import { useStartedJob } from '../jobs/startedJob';

/** Почему кнопка не нажимается, пока очередь уходит сама. */
export const SENDING_WHY =
  'Не нажимается, пока очередь уходит сама: шаблонные письма ушли бы раньше, чем их перепишет ' +
  'модель. Остановите отправку очереди — и переписывайте.';

/** «N писем» — числом и словом. */
function lettersOf(count: number): string {
  return `${formatNumber(count)} ${plural(count, 'письмо', 'письма', 'писем')}`;
}

/** Что окно говорит до нажатия: что сделает, сколько писем и что это вызовы модели. */
export function confirmText(count: number): string {
  return (
    `Модель заново перепишет ${lettersOf(count)} очереди, оставшихся шаблоном: каждое — ` +
    'отдельный вызов модели, тем же путём, что при сборке. Поправленные руками не тронет; ' +
    'откажет модель снова — письмо останется как было.'
  );
}

/** Итог задачи одной строкой: сколько переписано, что нет и почему, сколько осталось. */
export function rewriteLine(report: Record<string, unknown>): string {
  const left = (report.left ?? {}) as Record<string, number>;
  const said = [
    `Переписано ${formatNumber(Number(report.rewritten ?? 0))} из ` +
      `${formatNumber(Number(report.found ?? 0))}.`,
  ];
  const why = Object.entries(left).map(([reason, count]) => `${reason} — ${formatNumber(count)}`);
  if (why.length > 0) said.push(`Не переписано: ${why.join('; ')}.`);
  if (typeof report.stopped === 'string') said.push(`Остановлено: ${report.stopped}.`);
  const remaining = Number(report.remaining ?? 0);
  if (remaining > 0) said.push(`Шаблоном в очереди осталось ${lettersOf(remaining)}.`);
  return said.join(' ');
}

interface Props {
  stage: LetterStage;
  /** Аудитория Этапа 2; нет — по найденной ссылке. */
  audience?: LetterAudience;
  /** Сколько писем очереди оставлено шаблоном — число сервера (`templated`). */
  count: number;
  /** Очередь уходит сама: кнопка не нажимается (`SENDING_WHY`). */
  sending: boolean;
  onFinished: () => void;
}

/** Кнопка одной вкладки: номер задачи помнится под этапом и аудиторией — на смене вкладки
 *  экран ставит кнопку заново (`key`), и она читает номер своей вкладки. */
export function RewriteTemplated({ stage, audience = 'links', count, sending, onFinished }: Props) {
  const reasonId = useId();
  const { jobId, start } = useStartedJob(() => rewriteTemplated(stage, audience), {
    remember: `letters:last-rewrite:${stage}:${audience}`,
    queued: 'Переписывание ушло в очередь задач: каждое письмо — вызов модели, это минуты',
    refused: 'Не переписали',
    // Номер задачи вкладки постоянный — одна за раз: под ним уже новая задача.
    restart: true,
  });

  if (count === 0 && jobId === null) return null;
  return (
    <Stack gap={6}>
      {count > 0 ? (
        <ConfirmPopover
          message={confirmText(count)}
          confirm="Переписать"
          onConfirm={() => start.mutate()}
        >
          {(ask) => (
            <Group>
              <Button
                variant="light"
                className="press"
                disabled={sending}
                loading={start.isPending}
                aria-describedby={sending ? reasonId : undefined}
                onClick={ask}
              >
                Переписать шаблонные · {formatNumber(count)}
              </Button>
            </Group>
          )}
        </ConfirmPopover>
      ) : null}
      {count > 0 && sending ? (
        <Text id={reasonId} size="sm" c="dimmed">
          {SENDING_WHY}
        </Text>
      ) : null}
      {jobId !== null ? (
        <JobLine
          jobId={jobId}
          onFinished={onFinished}
          describe={rewriteLine}
          idle={(report) => Number(report.found ?? 0) === 0}
        />
      ) : null}
    </Stack>
  );
}
