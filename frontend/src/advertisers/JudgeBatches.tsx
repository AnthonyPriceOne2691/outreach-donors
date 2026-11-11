/**
 * Партии советов судьи в окне применения: кто, когда, какой полосой и сколько — и «Откатить».
 *
 * Партия — решения одного нажатия «Применить» (`crawl/judge_batches.py`): откат возвращает в очередь
 * те из них, что человек не переиграл. **Откат — с подтверждением у кнопки** (`ConfirmPopover`): он
 * меняет сотни строк разом, и слова подтверждения называют, что вернётся, а что останется.
 *
 * **Итог отката стоит в окне, а не уходит уведомлением**: в нём числа, по которым решают, что делать
 * дальше, — сколько вернулось и по скольким письмо уже ушло. Отказ сервера — там же, словами.
 *
 * Последние партии — три на виду, остальные за «Показать все · N» (`docs/UI_RULES.md`, 10.11.2026).
 */

import { Alert, Button, Group, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { fetchJudgeBatches, JUDGE_APPLY_KEY, revertJudgeBatch } from '../api/advertisersJudgeApply';
import type { JudgeBatch, JudgeReverted } from '../api/advertisersJudgeApply';
import { refusalOf } from '../api/client';
import { ConfirmPopover } from '../components/ConfirmPopover';
import { formatDateTime, formatNumber } from '../format';
import { bandWords, counted } from './judgeApplyWords';

/** Очередь проверки и всё под её ключом: строки, числа советов, план и сами партии. */
const QUEUE_KEY = ['advertisers'] as const;
/** Сколько партий на виду. */
const SHOWN = 3;

/** Что откат вернёт и что оставит — до нажатия. */
function revertWarning(batch: JudgeBatch): string {
  const back = 'Решения партии вернутся в очередь проверки; переигранные человеком останутся.';
  if (batch.band !== 'write') return back;
  return `${back} Рекламодатели и письма в очереди по этим «пишем» снимутся; где письмо уже ушло — решение останется.`;
}

/** Итог отката строками: нули не называются, кроме главного числа. */
export function revertedLines(result: JudgeReverted): string[] {
  const lines = [`В очередь вернулось: ${formatNumber(result.returned)}`];
  if (result.kept > 0) {
    lines.push(`Письмо уже ушло — решение осталось: ${formatNumber(result.kept)}`);
  }
  if (result.forgotten > 0) {
    lines.push(
      `Последний обход не нашёл — вернутся нерешёнными: ${formatNumber(result.forgotten)}`,
    );
  }
  if (result.removed > 0 || result.stopped > 0) {
    lines.push(
      `Снято рекламодателей: ${formatNumber(result.removed)}, писем из очереди: ${formatNumber(result.stopped)}`,
    );
  }
  return lines;
}

/** Что с партией сейчас: откатана — когда и сколько вернулось; нет — сколько решений в силе. */
function stateWords(batch: JudgeBatch): string {
  if (batch.reverted_at !== null) {
    return `откатана ${formatDateTime(batch.reverted_at)}: ${counted('вернулось', batch.returned)}`;
  }
  const standing = counted('в силе', batch.standing);
  return batch.left > 0 ? `${standing} · оборвана, ${counted('осталось', batch.left)}` : standing;
}

function BatchRow({
  batch,
  busy,
  reverting,
  onRevert,
}: {
  batch: JudgeBatch;
  /** Идёт применение или другой откат: вторая запись поверх не нужна. */
  busy: boolean;
  reverting: boolean;
  onRevert: () => void;
}) {
  return (
    <Group justify="space-between" align="flex-start" wrap="nowrap" gap="sm">
      <Stack gap={0}>
        <Text size="sm">
          {formatDateTime(batch.started_at)} · {bandWords(batch.band)} ·{' '}
          {counted('применено', batch.applied)}
        </Text>
        <Text size="xs" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
          {batch.by ?? 'без автора'} · {stateWords(batch)}
        </Text>
      </Stack>
      {batch.reverted_at === null ? (
        <ConfirmPopover message={revertWarning(batch)} confirm="Откатить" onConfirm={onRevert}>
          {(ask) => (
            <Button
              size="compact-sm"
              variant="default"
              className="press"
              loading={reverting}
              disabled={busy && !reverting}
              aria-label={`Откатить партию от ${formatDateTime(batch.started_at)}`}
              onClick={ask}
            >
              Откатить
            </Button>
          )}
        </ConfirmPopover>
      ) : null}
    </Group>
  );
}

export function JudgeBatches({ busy }: { busy: boolean }) {
  const client = useQueryClient();
  const [all, setAll] = useState(false);
  const [done, setDone] = useState<JudgeReverted | null>(null);
  const found = useQuery({
    queryKey: [...JUDGE_APPLY_KEY, 'batches'],
    queryFn: fetchJudgeBatches,
  });
  const revert = useMutation({
    mutationFn: revertJudgeBatch,
    onMutate: () => setDone(null),
    onSuccess: (result) => setDone(result),
    // Отказ тоже перечитывает: «откатывать нечего» значит, что список на экране устарел.
    onSettled: () => client.invalidateQueries({ queryKey: QUEUE_KEY }),
  });
  const batches = found.data ?? [];
  if (found.error !== null) {
    return (
      <Alert color="red" title="Партии не загрузились">
        {refusalOf(found.error)}
      </Alert>
    );
  }
  if (batches.length === 0) return null;
  const shown = all ? batches : batches.slice(0, SHOWN);
  return (
    <Stack gap="xs" aria-label="Партии советов судьи" role="group">
      <Text size="sm" fw={500}>
        Партии
      </Text>
      {done !== null && (
        <Alert color="green" title="Партия откатана">
          {revertedLines(done).map((line) => (
            <Text key={line} size="sm">
              {line}
            </Text>
          ))}
        </Alert>
      )}
      {revert.error !== null && (
        <Alert color="red" title="Партия не откатана">
          {refusalOf(revert.error)}
        </Alert>
      )}
      {shown.map((batch) => (
        <BatchRow
          key={batch.id}
          batch={batch}
          busy={busy || revert.isPending}
          reverting={revert.isPending && revert.variables === batch.id}
          onRevert={() => revert.mutate(batch.id)}
        />
      ))}
      {batches.length > SHOWN ? (
        <Button
          variant="subtle"
          size="compact-sm"
          className="press"
          aria-expanded={all}
          style={{ alignSelf: 'flex-start' }}
          onClick={() => setAll(!all)}
        >
          {all ? 'Свернуть' : `Показать все · ${formatNumber(batches.length)}`}
        </Button>
      ) : null}
    </Stack>
  );
}
