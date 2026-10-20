/**
 * Что в прогоне ждёт повтора — строками отчёта под лидами: адреса и очистка, почему словами,
 * когда повтор пойдёт сам и «Повторить сейчас» (разбор прогона сбора 20.10.2026: у «ждут очистки»
 * и «ждут адреса» не было ни причины, ни кнопки, и лиды висели неделю).
 *
 * **Срок — часами человека** (`formatTime` — сегодня, иначе дата и время): сервер отдаёт момент
 * UTC. Срок прошёл, а повтора ещё не было, — «срок повтора … наступил», и «Повторить сейчас» рядом.
 *
 * **«Повторить сейчас» — у группы, а не у строки**: повтор адресов ищет всех, кто ждёт повтора, а
 * очистка берёт всех ждущих лидов прогона тем же условием, что у прохода очистки. Кнопки нет, пока
 * прогон идёт (сервер ответил бы «уже идёт»), и у группы, где сейчас повторять нечего (первый
 * `unknown` ждёт дня перепроверки: платная проверка раньше срока — трата впустую). Без права «run»
 * — слова, как у «Продолжить».
 */

import { Alert, Button, Group, List, Stack, Text } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { formatDateTime, formatNumber, formatTime } from '../format';
import { jobRestarted } from '../jobs/JobLine';
import { notify } from '../notices';
import { againRun } from './segmentApi';
import type { SegmentAgain, SegmentRunDetail, SegmentWait } from './segmentApi';
import { SEGMENT_QUERY_KEY } from './segmentData';
import { isActive } from './segmentStage';

/** Группы ожидания по порядку отчёта: заголовок и что повторяет кнопка. */
const GROUPS: { kind: SegmentWait['kind']; title: string; again: SegmentAgain }[] = [
  { kind: 'address', title: 'Ждут адреса', again: 'addresses' },
  { kind: 'cleaning', title: 'Ждут очистки', again: 'cleaning' },
];

/** Срок повтора словами: «повтор около 14:35» — сегодня, иначе с датой; прошёл — «наступил». */
export function termWords(at: string, now: Date = new Date()): string {
  const when = new Date(at);
  const sameDay = when.toDateString() === now.toDateString();
  const moment = sameDay ? formatTime(at) : formatDateTime(at);
  return when.getTime() <= now.getTime()
    ? `срок повтора ${moment} наступил`
    : `повтор около ${moment}`;
}

/** Строка ожидания: почему, сколько и когда повтор сам. Тире и число — неразрывно с последним
 *  словом: на телефоне число одно на строке читалось оторванным (снимок стенда 20.10.2026). */
export function waitLine(wait: SegmentWait, now: Date = new Date()): string {
  const said = `${wait.words}\u00a0—\u00a0${formatNumber(wait.count)}`;
  return wait.at === null ? said : `${said}, ${termWords(wait.at, now)}`;
}

function useAgain(run: number, what: SegmentAgain) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => againRun(run, what),
    onSuccess: async (queued) => {
      notify({ message: `Повтор прогона №${queued.run_id} в очереди задач`, color: 'green' });
      await jobRestarted(client, queued.job_id);
      await client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY });
    },
  });
}

/** «Повторить сейчас» — или почему кнопки нет у этой учётки. */
function AgainButton({ busy, press }: { busy: boolean; press: () => void }) {
  const { can } = useSession();
  if (!can('run')) {
    return (
      <Text size="sm" c="dimmed">
        Повторяет сотрудник с правом «{PERMISSION_TITLES.run}».
      </Text>
    );
  }
  return (
    <Button size="compact-sm" variant="default" className="press" loading={busy} onClick={press}>
      Повторить сейчас
    </Button>
  );
}

function WaitGroup({
  detail,
  title,
  what,
  rows,
}: {
  detail: SegmentRunDetail;
  title: string;
  what: SegmentAgain;
  rows: SegmentWait[];
}) {
  const again = useAgain(detail.run.id, what);
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  const offered = !isActive(detail.run) && rows.some((row) => row.again);
  return (
    <Stack gap={2}>
      <Group gap="xs" wrap="wrap">
        <Text size="sm" fw={500}>
          {title} — {formatNumber(total)}
        </Text>
        {offered && <AgainButton busy={again.isPending} press={() => again.mutate()} />}
      </Group>
      <List size="sm" spacing={2} aria-label={title}>
        {rows.map((row) => (
          <List.Item key={row.words}>{waitLine(row)}</List.Item>
        ))}
      </List>
      {again.error !== null && (
        <Alert color="red" title="Повтор не поставлен">
          {refusalOf(again.error)}
        </Alert>
      )}
    </Stack>
  );
}

export function SegmentWaits({ detail }: { detail: SegmentRunDetail }) {
  const waits = detail.waits ?? [];
  if (waits.length === 0) return null;
  return (
    <Stack gap={6}>
      {GROUPS.map(({ kind, title, again }) => {
        const rows = waits.filter((wait) => wait.kind === kind);
        if (rows.length === 0) return null;
        return <WaitGroup key={kind} detail={detail} title={title} what={again} rows={rows} />;
      })}
    </Stack>
  );
}
