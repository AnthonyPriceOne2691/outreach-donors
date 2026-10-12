/**
 * Отчёт прогона гипотезы: ход, сколько сайтов нашла выдача, принято, на проверку, отсечено
 * с причинами словами, без адреса, лидов создано — и решение человека по «на проверку».
 *
 * **Числа — из сайтов прогона, а не из итога задачи** (`GET /sales/segment/runs/{N}`):
 * решение человека и продолжение меняют их после задачи, и отчёт показывает, что есть
 * сейчас. Пока прогон идёт, отчёт опрашивается сам (`useRun`), ход задачи — строкой
 * `JobLine`.
 *
 * **Человек решает только «на проверку»**: «Принять» — сайт пойдёт к поиску адреса
 * продолжением прогона, «Отказ» — отсечён. Вердикт судьи остаётся рядом: в наблюдении
 * «не подходит» судьи лежит здесь же, а решает человек — по их расхождению меряют судью.
 * Продолжение — кнопкой: поиск адреса стоит денег (Hunter), и запускает его человек
 * с правом «run».
 */

import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  List,
  Loader,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { formatNumber } from '../format';
import { JobLine, jobRestarted } from '../jobs/JobLine';
import { notify } from '../notices';
import { FixedTable } from './FixedTable';
import type { Column } from './FixedTable';
import { decideSite, resumeRun } from './segmentApi';
import type { SegmentCandidate, SegmentCount, SegmentRunDetail } from './segmentApi';
import {
  leadsLine,
  MODE_WORDS,
  runJobLine,
  SEGMENT_QUERY_KEY,
  STAGE_WORDS,
  useRun,
  VERDICT_WORDS,
} from './segmentData';

const COLUMNS: Column[] = [
  { title: 'Сайт', width: '15rem' },
  { title: 'Судья', width: '7.5rem' },
  { title: 'Почему на проверке', start: true },
  { title: 'Решение', width: '12.5rem' },
];

export const REVIEW_MIN_WIDTH = 900;

function Counts({ title, rows }: { title: string; rows: SegmentCount[] }) {
  if (rows.length === 0) return null;
  return (
    <Stack gap={2}>
      <Text size="sm" fw={500}>
        {title}
      </Text>
      <List size="sm" spacing={2} aria-label={title}>
        {rows.map((row) => (
          <List.Item key={row.words}>
            {row.words} — {formatNumber(row.count)}
          </List.Item>
        ))}
      </List>
    </Stack>
  );
}

function Tiles({ detail }: { detail: SegmentRunDetail }) {
  const { summary } = detail;
  return (
    <SimpleGrid cols={{ base: 2, xs: 3, md: 6 }} spacing="sm">
      <Metric title="Сайтов из выдачи" value={formatNumber(summary.hosts)} />
      <Metric title="Принято" value={formatNumber(summary.accepted)} />
      <Metric
        title="На проверку"
        value={formatNumber(summary.review)}
        color={summary.review > 0 ? 'yellow' : undefined}
      />
      <Metric title="Отсечено" value={formatNumber(summary.rejected)} />
      <Metric title="Без адреса" value={formatNumber(summary.no_address)} />
      <Metric title="Лидов создано" value={formatNumber(summary.leads)} />
    </SimpleGrid>
  );
}

function Why({ site }: { site: SegmentCandidate }) {
  return (
    <Stack gap={2}>
      <Text size="sm" className="segmentReason">
        {site.reason ?? 'причина не записана'}
      </Text>
      {site.quote !== null && (
        <Text size="xs" c="dimmed">
          «{site.quote}»
        </Text>
      )}
    </Stack>
  );
}

function ReviewRow({
  site,
  onDecide,
  busy,
}: {
  site: SegmentCandidate;
  onDecide: (accept: boolean) => void;
  busy: boolean;
}) {
  return (
    <Table.Tr>
      <Table.Td>
        <Stack gap={0}>
          {site.page_url !== null ? (
            <Anchor
              href={site.page_url}
              target="_blank"
              rel="noreferrer"
              size="sm"
              c="var(--ink)"
              underline="always"
              className="inkLink"
            >
              {site.host}
            </Anchor>
          ) : (
            <Text size="sm">{site.host}</Text>
          )}
          {site.keyword !== null && (
            <Text size="xs" c="dimmed" truncate>
              по ключу «{site.keyword}»
            </Text>
          )}
        </Stack>
      </Table.Td>
      <Table.Td>
        <Text size="sm">
          {site.unanswered ? 'не ответил' : VERDICT_WORDS[site.verdict ?? 'pending']}
        </Text>
      </Table.Td>
      <Table.Td ta="left">
        <Why site={site} />
      </Table.Td>
      <Table.Td>
        <Group gap="xs" justify="center" wrap="nowrap">
          <Button
            size="compact-sm"
            className="press"
            disabled={busy}
            onClick={() => onDecide(true)}
          >
            Принять
          </Button>
          <Button
            size="compact-sm"
            variant="default"
            className="press"
            disabled={busy}
            onClick={() => onDecide(false)}
          >
            Отказ
          </Button>
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}

function Review({ detail }: { detail: SegmentRunDetail }) {
  const client = useQueryClient();
  const decide = useMutation({
    mutationFn: ({ site, accept }: { site: number; accept: boolean }) => decideSite(site, accept),
    onSuccess: () => void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY }),
  });
  if (detail.review.length === 0) return null;
  return (
    <Stack gap="xs">
      <Title order={5}>На проверку — решает человек</Title>
      <Text size="sm" c="dimmed">
        «Принять» — сайт пойдёт к поиску адреса, когда прогон продолжат; «Отказ» — отсечён.
      </Text>
      {decide.error !== null && (
        <Alert color="red" title="Решение не записано">
          {refusalOf(decide.error)}
        </Alert>
      )}
      <FixedTable
        columns={COLUMNS}
        minWidth={REVIEW_MIN_WIDTH}
        className="segmentReview"
        label="Сайты на проверку"
        rows={detail.review.map((site) => (
          <ReviewRow
            key={site.id}
            site={site}
            busy={decide.isPending}
            onDecide={(accept) => decide.mutate({ site: site.id, accept })}
          />
        ))}
      />
    </Stack>
  );
}

/** Продолжить прогон: после остановки и для принятых, кому ещё не искали адрес. */
function Resume({ detail }: { detail: SegmentRunDetail }) {
  const client = useQueryClient();
  const { can } = useSession();
  const resume = useMutation({
    mutationFn: () => resumeRun(detail.run.id),
    onSuccess: (queued) => {
      void jobRestarted(client, queued.job_id);
      void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY });
      notify({ message: `Прогон №${queued.run_id} продолжается`, color: 'green' });
    },
  });
  const stopped = detail.run.stage === 'stopped';
  if (!stopped && detail.waiting_address === 0) return null;
  const label = stopped
    ? 'Продолжить прогон'
    : `Найти адреса принятым · ${formatNumber(detail.waiting_address)}`;
  return (
    <Stack gap={6}>
      {can('run') ? (
        <Group>
          <Button className="press" loading={resume.isPending} onClick={() => resume.mutate()}>
            {label}
          </Button>
        </Group>
      ) : (
        <Text size="sm" c="dimmed">
          Продолжает прогон сотрудник с правом «{PERMISSION_TITLES.run}».
        </Text>
      )}
      {resume.error !== null && (
        <Alert color="red" title="Прогон не продолжен">
          {refusalOf(resume.error)}
        </Alert>
      )}
    </Stack>
  );
}

function Report({ detail }: { detail: SegmentRunDetail }) {
  const client = useQueryClient();
  const { run } = detail;
  return (
    <Stack gap="sm">
      <Group gap="xs" wrap="wrap">
        <Title order={5}>Прогон №{run.id}</Title>
        <Badge
          variant="light"
          color={run.stage === 'stopped' ? 'yellow' : run.stage === 'done' ? 'green' : 'gray'}
        >
          {STAGE_WORDS[run.stage]}
        </Badge>
      </Group>
      <Text size="sm" c="dimmed">
        {MODE_WORDS[run.mode]}.
      </Text>
      {run.error !== null && (
        <Alert color="yellow" title="Прогон остановлен">
          {run.error}
        </Alert>
      )}
      {run.job_id !== null && run.stage !== 'done' && (
        <JobLine
          jobId={run.job_id}
          describe={runJobLine}
          onFinished={() => void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY })}
        />
      )}
      <Tiles detail={detail} />
      <Text size="sm">{leadsLine(detail)}</Text>
      <Resume detail={detail} />
      <Counts title="Отсечены — почему" rows={detail.rejected} />
      <Counts title="Без адреса — почему" rows={detail.no_address} />
      {detail.estimate_lines.length > 0 && (
        <Stack gap={2}>
          <Text size="sm" fw={500}>
            Смета на запуске
          </Text>
          {detail.estimate_lines.map((line) => (
            <Text key={line} size="sm" c="dimmed">
              {line}
            </Text>
          ))}
        </Stack>
      )}
      <Review detail={detail} />
    </Stack>
  );
}

export function SegmentReport({ run }: { run: number }) {
  const found = useRun(run);
  if (found.data === undefined) {
    if (!found.error) return <Loader aria-label="Загружаем отчёт прогона" m="md" />;
    return (
      <Alert color="red" title="Отчёт прогона не загрузился">
        {refusalOf(found.error)}
      </Alert>
    );
  }
  return <Report detail={found.data} />;
}
