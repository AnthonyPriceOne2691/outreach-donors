/**
 * Помощник описания сегмента — решение владельца 16.10.2026: «нужно делать его, чтобы он
 * работал… обязательно», от описания зависит конечный результат сбора. Судья читает описание
 * буквально; помощник переписывает черновик так, как его читает судья, — кто подходит (бизнес,
 * который сам делает своё), кто нет, — и спрашивает, что в черновике не ясно
 * (`backend/features/sales/segment_helper.py`).
 *
 * Два места:
 * - **«Помочь с описанием»** под полем описания — на «Сборе базы» и в окне гипотезы;
 * - **проверка описания при смете** (`SegmentCheck`): описание расплывчатое — предупреждение и
 *   предложение помощника рядом со сметой; «Взять в описание» открывает правку описания.
 *
 * **Человек правит и сохраняет сам:** «Взять» кладёт предложение в поле, а не в гипотезу —
 * сохраняет кнопка формы. Ответ на тот же черновик сервер отдаёт из кэша даром, а экран держит
 * его в памяти запросов под текстом черновика: смета, пересчитанная после галочки, помощника
 * второй раз не спрашивает, и кнопка по уже проверенному описанию отвечает сразу.
 */

import { Alert, Button, Group, List, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { helpSegment } from './segmentApi';
import type { SegmentHelp } from './segmentApi';

/** Ответы помощника в памяти запросов — под текстом черновика без пробелов по краям. */
export const HELP_QUERY_KEY = ['sales', 'segment-helper'] as const;

function helpKey(text: string) {
  return [...HELP_QUERY_KEY, text.trim()] as const;
}

/** Вывод помощника одной строкой: расплывчат ли черновик (он назвал, что собьёт судью) и что это
 *  значит для судьи. */
export function verdictOf(found: SegmentHelp): string {
  return found.loose
    ? 'Черновик расплывчатый: судья прочтёт его буквально и будет пропускать лишнее.'
    : 'Черновик уже конкретный — можно оставить как есть.';
}

/** Строки ответа помощника под подписью: что собьёт судью или что уточнить. */
function Lines({ title, lines }: { title: string; lines: string[] }) {
  return (
    <Stack gap={2}>
      <Text size="sm" fw={600}>
        {title}
      </Text>
      <List size="sm" spacing={2}>
        {lines.map((line) => (
          <List.Item key={line}>{line}</List.Item>
        ))}
      </List>
    </Stack>
  );
}

/** Предложение помощника: что собьёт судью, описание, вопросы к черновику и кнопки — «взять» и
 *  «скрыть». */
function Proposal({
  found,
  take,
  onTake,
  onHide,
}: {
  found: SegmentHelp;
  take: string;
  onTake: (text: string) => void;
  onHide: () => void;
}) {
  return (
    <Stack gap={6} aria-label="Предложение помощника">
      {found.problems.length > 0 && <Lines title="Что собьёт судью" lines={found.problems} />}
      <Text size="sm" fw={600}>
        Помощник предлагает
      </Text>
      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
        {found.description}
      </Text>
      {found.unclear.length > 0 && <Lines title="Что уточнить в черновике" lines={found.unclear} />}
      <Group gap="sm">
        <Button size="compact-sm" className="press" onClick={() => onTake(found.description)}>
          {take}
        </Button>
        <Button variant="default" size="compact-sm" className="press" onClick={onHide}>
          Скрыть
        </Button>
      </Group>
    </Stack>
  );
}

/** «Помочь с описанием» под полем описания: ответ — под кнопкой, в поле — только по «Взять». */
export function SegmentHelper({
  draft,
  onTake,
}: {
  draft: string;
  onTake: (text: string) => void;
}) {
  const client = useQueryClient();
  const ask = useMutation({
    mutationFn: (text: string) => helpSegment(text),
    onSuccess: (found, text) => client.setQueryData(helpKey(text), found),
  });
  const empty = draft.trim() === '';
  const found = ask.data;
  const moved = ask.variables !== undefined && ask.variables.trim() !== draft.trim();
  return (
    <Stack gap={6}>
      <Group gap="sm" wrap="wrap">
        <Button
          variant="default"
          size="compact-sm"
          className="press"
          loading={ask.isPending}
          disabled={empty}
          onClick={() => ask.mutate(draft)}
        >
          Помочь с описанием
        </Button>
        <Text size="xs" c="dimmed">
          {empty
            ? 'Напишите черновик — помощник перепишет его так, как читает судья'
            : 'Помощник перепишет черновик так, как его читает судья, и спросит, что не ясно'}
        </Text>
      </Group>
      {ask.error !== null && (
        <Alert color="red" title="Помощник не ответил">
          {refusalOf(ask.error)}
        </Alert>
      )}
      {found !== undefined && (
        <Stack gap={6} className="hairline" pt="xs">
          <Text size="sm" fw={600}>
            {verdictOf(found)}
          </Text>
          {moved && (
            <Text size="xs" c="dimmed">
              Ответ — на черновик до правки: поле с тех пор поправили.
            </Text>
          )}
          <Proposal
            found={found}
            take="Взять этот текст"
            onTake={(text) => {
              onTake(text);
              ask.reset();
            }}
            onHide={() => ask.reset()}
          />
        </Stack>
      )}
    </Stack>
  );
}

/** Проверка описания при смете: расплывчатое — предупреждение с предложением помощника,
 *  конкретное — ничего. Помощник не ответил — строка словами: смета посчитана и без проверки. */
export function SegmentCheck({
  segment,
  onTake,
}: {
  segment: string;
  onTake: (text: string) => void;
}) {
  const text = segment.trim();
  const [hidden, setHidden] = useState<string | null>(null);
  const check = useQuery({
    queryKey: helpKey(text),
    queryFn: () => helpSegment(text),
    enabled: text !== '',
    staleTime: Infinity,
    retry: false,
  });
  if (text === '' || hidden === text) return null;
  if (check.error !== null) {
    return (
      <Text size="xs" c="dimmed">
        Описание сегмента помощник не проверил: {refusalOf(check.error)}
      </Text>
    );
  }
  if (check.data === undefined) {
    return (
      <Text size="xs" c="dimmed">
        Помощник проверяет описание сегмента…
      </Text>
    );
  }
  if (!check.data.loose) return null;
  return (
    <Alert color="yellow" title="Описание сегмента расплывчатое">
      <Stack gap={6}>
        <Text size="sm">
          Судья читает описание буквально и будет пропускать лишнее — справочники, СМИ, компании
          других отраслей.
        </Text>
        <Proposal
          found={check.data}
          take="Взять в описание"
          onTake={onTake}
          onHide={() => setHidden(text)}
        />
      </Stack>
    </Alert>
  );
}
