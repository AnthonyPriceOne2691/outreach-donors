/**
 * Отчёт прогона гипотезы: ход, сколько сайтов нашла выдача, принято, на проверку, отсечено
 * с причинами словами, без адреса, лидов создано — и решение человека по «на проверку».
 *
 * **Числа — из сайтов прогона, а не из итога задачи** (`GET /sales/segment/runs/{N}`):
 * решение человека и продолжение меняют их после задачи, и отчёт показывает, что есть
 * сейчас. Пока прогон идёт (решает сервер по очереди задач), отчёт опрашивается сам
 * (`useRun`), ход — строкой `SegmentProgress`, исход задачи — строкой `JobLine`. Плитки
 * сходятся и во время судьи: «ещё не судили» — своим числом.
 *
 * **Человек решает только «на проверку»**: «Принять» — сайт пойдёт к поиску адреса
 * продолжением прогона, «Отказ» — отсечён. Вердикт судьи остаётся рядом: в наблюдении
 * «не подходит» судьи лежит здесь же, а решает человек — по их расхождению меряют судью.
 * Решённая строка стоит на месте с пометкой, пока открыт отчёт: двойной клик не уходит
 * вторым решением, а нижние строки не поднимаются под курсор (правило «Отбора»).
 * Продолжение — кнопкой: поиск адреса стоит денег (Hunter), и запускает его человек
 * с правом «run»; пока прогон идёт, кнопки нет — принятым адреса ищет он сам.
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
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { Seams } from '../components/Seams';
import { formatNumber } from '../format';
import { JobLine, jobRestarted } from '../jobs/JobLine';
import { notify } from '../notices';
import { FixedTable } from './FixedTable';
import type { Column } from './FixedTable';
import { decideSite, resumeRun } from './segmentApi';
import type { SegmentCandidate, SegmentCount, SegmentRunDetail } from './segmentApi';
import {
  knownSites,
  leadsLine,
  MODE_WORDS,
  runFacts,
  runJobLine,
  SEGMENT_QUERY_KEY,
  useRun,
  useRunSettled,
  VERDICT_WORDS,
} from './segmentData';
import { SegmentKeywords } from './SegmentKeywords';
import { SegmentProgress } from './SegmentProgress';
import { SegmentSpend } from './SegmentSpend';
import { isActive, stageBadge } from './segmentStage';

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
  const pending = summary.pending ?? detail.lanes.pending ?? 0;
  return (
    <SimpleGrid cols={{ base: 2, xs: 4, md: 7 }} spacing="sm">
      <Metric title="Сайтов из выдачи" value={formatNumber(summary.hosts)} />
      <Metric title="Принято" value={formatNumber(summary.accepted)} />
      <Metric
        title="На проверку"
        value={formatNumber(summary.review)}
        color={summary.review > 0 ? 'yellow' : undefined}
      />
      <Metric title="Отсечено" value={formatNumber(summary.rejected)} />
      <Metric title="Ещё не судили" value={formatNumber(pending)} />
      <Metric title="Без адреса" value={formatNumber(summary.no_address)} />
      <Metric title="Лидов создано" value={formatNumber(summary.leads)} />
    </SimpleGrid>
  );
}

/** Сайты, которые гипотеза уже собирала: их не судят и не ищут снова. */
function Known({ detail }: { detail: SegmentRunDetail }) {
  const known = knownSites(detail);
  if (known === 0) return null;
  return (
    <Text size="sm" c="dimmed">
      Уже в сборе гипотезы: {formatNumber(known)} — эти сайты нашли её прошлые прогоны, их не судим
      и не ищем снова.
    </Text>
  );
}

/** Почему сайт на проверке: причина — целиком, переносом в своей колонке (ячейка `wrapCell`);
 *  цитата сайта — двумя строками, целиком — в подсказке, как цитата судьи доноров
 *  (`components/JudgeVerdict`): в 30 слов она растягивала бы строку на пять. */
function Why({ site }: { site: SegmentCandidate }) {
  return (
    <Stack gap={2}>
      <Text size="sm" className="segmentReason">
        {site.reason ?? 'причина не записана'}
      </Text>
      {site.quote !== null && (
        <Text size="xs" c="dimmed" lineClamp={2} title={`«${site.quote}»`}>
          «{site.quote}»
        </Text>
      )}
    </Stack>
  );
}

/** Решение этого экрана по сайту: чем решён и записан ли уже сервером. */
interface Held {
  site: SegmentCandidate;
  accept: boolean;
  written: boolean;
}

function Decided({ held }: { held: Held }) {
  const words = held.accept ? 'принят' : 'отсечён';
  return (
    <Text size="sm" ta="center" c="dimmed">
      {held.written ? words : `${words} — записываем…`}
    </Text>
  );
}

function ReviewRow({
  site,
  held,
  onDecide,
}: {
  site: SegmentCandidate;
  held: Held | undefined;
  onDecide: (accept: boolean) => void;
}) {
  return (
    <Table.Tr>
      {/* Домен — по швам (`components/Seams`), а не под соседнюю колонку; ключ — одной
          строкой, целиком — в подсказке. */}
      <Table.Td className="cellName">
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
              <Seams text={site.host} />
            </Anchor>
          ) : (
            <Text size="sm">
              <Seams text={site.host} />
            </Text>
          )}
          {site.keyword !== null && (
            <Text size="xs" c="dimmed" truncate title={`по ключу «${site.keyword}»`}>
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
      {/* Причина переносится в своей колонке: в строке без переноса длинная уезжала под
          кнопки «Решения» (находка стенда 14.10.2026). */}
      <Table.Td className="wrapCell" ta="left">
        <Why site={site} />
      </Table.Td>
      <Table.Td>
        {held !== undefined ? (
          <Decided held={held} />
        ) : (
          <Group gap="xs" justify="center" wrap="nowrap">
            <Button size="compact-sm" className="press" onClick={() => onDecide(true)}>
              Принять
            </Button>
            <Button
              size="compact-sm"
              variant="default"
              className="press"
              onClick={() => onDecide(false)}
            >
              Отказ
            </Button>
          </Group>
        )}
      </Table.Td>
    </Table.Tr>
  );
}

/** Строки «на проверку»: сайты сервера и решённые здесь — на своих местах, по порядку. */
function reviewRows(detail: SegmentRunDetail, held: Record<number, Held>): SegmentCandidate[] {
  const shown = new Map(detail.review.map((site) => [site.id, site]));
  for (const { site } of Object.values(held)) if (!shown.has(site.id)) shown.set(site.id, site);
  return [...shown.values()].sort((a, b) => a.id - b.id);
}

function Review({ detail }: { detail: SegmentRunDetail }) {
  const client = useQueryClient();
  const [held, setHeld] = useState<Record<number, Held>>({});
  const decide = useMutation({
    mutationFn: ({ site, accept }: { site: SegmentCandidate; accept: boolean }) =>
      decideSite(site.id, accept),
    onMutate: ({ site, accept }) =>
      setHeld((known) => ({ ...known, [site.id]: { site, accept, written: false } })),
    onSuccess: (_written, { site, accept }) => {
      setHeld((known) => ({ ...known, [site.id]: { site, accept, written: true } }));
      void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY });
    },
    onError: (_failure, { site }) =>
      setHeld((known) => {
        const rest = { ...known };
        delete rest[site.id];
        return rest;
      }),
  });
  const rows = reviewRows(detail, held);
  if (rows.length === 0) return null;
  const total = detail.summary.review;
  return (
    <Stack gap="xs">
      <Title order={5}>На проверку — решает человек</Title>
      <Text size="sm" c="dimmed">
        «Принять» — сайт пойдёт к поиску адреса, когда прогон продолжат; «Отказ» — отсечён.
      </Text>
      {detail.review.length < total && (
        <Text size="sm" c="dimmed">
          Показаны первые {formatNumber(detail.review.length)} из {formatNumber(total)} — следующие
          встанут сюда, когда решите эти.
        </Text>
      )}
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
        rows={rows.map((site) => (
          <ReviewRow
            key={site.id}
            site={site}
            held={held[site.id]}
            onDecide={(accept) => decide.mutate({ site, accept })}
          />
        ))}
      />
    </Stack>
  );
}

/** Продолжить прогон: после остановки (и когда задачи прогона нет) и для принятых, кому ещё
 *  не искали адрес. Пока прогон идёт, кнопки нет: принятым адреса найдёт он сам, а сервер
 *  ответил бы «уже идёт». Кнопка ждёт, пока отчёт перечитан: прогон уже «идёт» — её нет. */
function Resume({ detail }: { detail: SegmentRunDetail }) {
  const client = useQueryClient();
  const { can } = useSession();
  const resume = useMutation({
    mutationFn: () => resumeRun(detail.run.id),
    onSuccess: async (queued) => {
      notify({ message: `Прогон №${queued.run_id} продолжается`, color: 'green' });
      await jobRestarted(client, queued.job_id);
      await client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY });
    },
  });
  if (isActive(detail.run)) return null;
  const stopped = detail.run.stage === 'stopped' || Boolean(detail.run.watch?.stalled);
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

/** Строка задачи — там, где она говорит то, чего не говорит ход: когда следующая попытка
 *  задачи, ждущей повтора, и сколько их осталось. «В очереди» и «идёт» ход уже сказал. У
 *  сервера старше хода — как раньше: у всякого не готового прогона. */
function jobLineShown(run: SegmentRunDetail['run']): boolean {
  if (run.watch === undefined) return run.stage !== 'done';
  return run.watch?.job === 'scheduled';
}

/** Значок стадии: идёт — где сейчас; остановлен без задачи — «остановлен», как и словами. */
function StageBadge({ detail }: { detail: SegmentRunDetail }) {
  const { title, color } = stageBadge(detail.run);
  return (
    <Badge variant="light" color={color}>
      {title}
    </Badge>
  );
}

function Report({ detail }: { detail: SegmentRunDetail }) {
  const client = useQueryClient();
  const { run } = detail;
  useRunSettled(detail);
  return (
    <Stack gap="sm">
      <Group gap="xs" wrap="wrap">
        <Title order={5}>Прогон №{run.id}</Title>
        <StageBadge detail={detail} />
      </Group>
      <Text size="sm" c="dimmed">
        {runFacts(run)}
      </Text>
      <Text size="sm" c="dimmed">
        {MODE_WORDS[run.mode]}.
      </Text>
      <SegmentProgress run={run} />
      {run.job_id !== null && jobLineShown(run) && (
        <JobLine
          jobId={run.job_id}
          describe={runJobLine}
          onFinished={() => void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY })}
        />
      )}
      <Tiles detail={detail} />
      <Known detail={detail} />
      <Text size="sm">{leadsLine(detail)}</Text>
      <Resume detail={detail} />
      <Counts title="Отсечены — почему" rows={detail.rejected} />
      <Counts title="Без адреса — почему" rows={detail.no_address} />
      <SegmentKeywords detail={detail} />
      <SegmentSpend detail={detail} />
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
      <Review key={run.id} detail={detail} />
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
