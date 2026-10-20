/**
 * Что в прогоне ждёт повтора — строками отчёта под лидами: адреса и очистка, почему словами,
 * срок повтора и «Повторить сейчас» (разбор прогона сбора 20.10.2026: у «ждут очистки» и «ждут
 * адреса» не было ни причины, ни кнопки, и лиды висели неделю).
 *
 * **Срок — часами человека** (`termWords`): впереди — «срок ещё не настал — повтор сам около …»,
 * прошёл, а повтора ещё не было, — «срок повтора … наступил».
 *
 * **«Повторить сейчас» берёт только то, чей срок настал** (ревью 20.10.2026: кнопка, бравшая
 * всех, сжигала расписание повторов). Что она возьмёт и до скольких платных запросов Hunter —
 * числом сервера рядом с кнопкой (`SegmentOffer`: то же условие, что у прохода повтора), а
 * платный повтор — через подтверждение у кнопки, как прочие действия с последствиями
 * (`ConfirmPopover`: фокус на «Отмене»). Кнопки нет, пока прогон идёт (сервер ответил бы «уже
 * идёт»), и у группы, где сейчас повторять нечего. Без права «run» — слова, как у «Продолжить».
 */

import { Alert, Button, Group, List, Stack, Text } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { ConfirmPopover } from '../components/ConfirmPopover';
import { formatNumber, plural } from '../format';
import { jobRestarted } from '../jobs/JobLine';
import { notify } from '../notices';
import { againRun } from './segmentApi';
import type { SegmentAgain, SegmentOffer, SegmentRunDetail, SegmentWait } from './segmentApi';
import { SEGMENT_QUERY_KEY } from './segmentData';
import { isActive } from './segmentStage';
import { termWords } from './termWords';

/** Группы ожидания по порядку отчёта: заголовок и что повторяет кнопка. */
const GROUPS: { kind: SegmentWait['kind']; title: string; again: SegmentAgain }[] = [
  { kind: 'address', title: 'Ждут адреса', again: 'addresses' },
  { kind: 'cleaning', title: 'Ждут очистки', again: 'cleaning' },
];

/** Строка ожидания: почему, сколько и срок. Тире и число — неразрывно с последним словом: на
 *  телефоне число одно на строке читалось оторванным (снимок стенда 20.10.2026). */
export function waitLine(wait: SegmentWait, now: Date = new Date()): string {
  const said = `${wait.words}\u00a0—\u00a0${formatNumber(wait.count)}`;
  return wait.at === null ? said : `${said}, ${termWords(wait.at, now)}`;
}

/** Платные запросы повтора словами: «до 3 поисков и до 3 проверок Hunter», «до 1 проверки
 *  Hunter»; платных нет — пусто. */
function paidWords(found: SegmentOffer): string | null {
  const parts = [
    found.searches > 0
      ? `до ${formatNumber(found.searches)} ${plural(found.searches, 'поиска', 'поисков', 'поисков')}`
      : null,
    found.checks > 0
      ? `до ${formatNumber(found.checks)} ${plural(found.checks, 'проверки', 'проверок', 'проверок')}`
      : null,
  ].filter((part): part is string => part !== null);
  return parts.length > 0 ? `${parts.join(' и ')} Hunter` : null;
}

/** Что возьмёт кнопка — верхней границей: «поищет до 2 сайтов, до 2 поисков и до 2 проверок
 *  Hunter», «перепроверит до 3 лидов, без платных запросов». */
export function offerWords(found: SegmentOffer): string {
  const taken =
    found.what === 'cleaning'
      ? `перепроверит до ${formatNumber(found.count)} ${plural(found.count, 'лида', 'лидов', 'лидов')}`
      : `поищет до ${formatNumber(found.count)} ${plural(found.count, 'сайта', 'сайтов', 'сайтов')}`;
  return `${taken}, ${paidWords(found) ?? 'без платных запросов'}`;
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

/** Вопрос подтверждения у платного повтора: сколько самое большее и почему обычно меньше. */
export function paidQuestion(found: SegmentOffer): string {
  const why =
    found.what === 'cleaning'
      ? 'каждый лид — не больше одной проверки'
      : 'поиск платит только там, где бесплатные ступени адреса не нашли, проверка — за найденный адрес';
  return `Повторить сейчас? Повтор платный: ${paidWords(found) ?? ''} — ${why}.`;
}

/** «Повторить сейчас» и что он возьмёт — или почему кнопки нет у этой учётки. Платный повтор —
 *  через подтверждение у кнопки (`ConfirmPopover`: фокус на «Отмене»). */
function AgainButton({
  found,
  busy,
  press,
}: {
  found: SegmentOffer;
  busy: boolean;
  press: () => void;
}) {
  const { can } = useSession();
  if (!can('run')) {
    return (
      <Text size="sm" c="dimmed">
        Повторяет сотрудник с правом «{PERMISSION_TITLES.run}».
      </Text>
    );
  }
  const button = (onClick: () => void) => (
    <Button size="compact-sm" variant="default" className="press" loading={busy} onClick={onClick}>
      Повторить сейчас
    </Button>
  );
  return (
    <>
      {paidWords(found) !== null ? (
        <ConfirmPopover message={paidQuestion(found)} confirm="Повторить" onConfirm={press}>
          {button}
        </ConfirmPopover>
      ) : (
        button(press)
      )}
      <Text size="sm" c="dimmed">
        {offerWords(found)}
      </Text>
    </>
  );
}

interface GroupProps {
  detail: SegmentRunDetail;
  title: string;
  what: SegmentAgain;
  rows: SegmentWait[];
}

function WaitGroup({ detail, title, what, rows }: GroupProps) {
  const again = useAgain(detail.run.id, what);
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  const found = isActive(detail.run) ? undefined : detail.again?.find((one) => one.what === what);
  return (
    <Stack gap={2}>
      <Group gap="xs" wrap="wrap">
        <Text size="sm" fw={500}>
          {title} — {formatNumber(total)}
        </Text>
        {found !== undefined && (
          <AgainButton found={found} busy={again.isPending} press={() => again.mutate()} />
        )}
      </Group>
      <List size="sm" spacing={2} aria-label={title}>
        {rows.map((row) => (
          <List.Item key={`${row.words}:${String(row.again)}`}>{waitLine(row)}</List.Item>
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
