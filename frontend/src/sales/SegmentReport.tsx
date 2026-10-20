/**
 * Отчёт прогона гипотезы: ход, сколько сайтов нашла выдача, принято, на проверку, судья отказал бы,
 * отсечено, без адреса, лидов создано — и что ждёт человека.
 *
 * **Компактно — за один-два экрана** (15.10.2026; замер стенда до правки — прогон в 70 сайтов, из
 * них 20 на проверку: 4 980 px на 1440 и 7 676 на 390, почти половина — таблица «На проверку»):
 * сразу — шапка, ход, плитки, «На проверку N — решить», лиды и продолжение; списки и расход — по
 * кнопке, в окнах. Плитки сайтов и полос, «Сайты прогона · N» у заголовка и «На проверку N —
 * решить» — ссылки: открывают окно «Сайты прогона» на своей вкладке (`SegmentSitesWindow`), вид
 * окна — в адресе. «Расход и смета» — своё окно (`SpendWindow`). Почему отсечены и почему без адреса —
 * одной свёрнутой строкой: прочесть её нужно и на телефоне, а подсказки по наведению там нет.
 *
 * **Числа — из сайтов прогона, а не из итога задачи** (`GET /sales/segment/runs/{N}`):
 * решение человека и продолжение меняют их после задачи, и отчёт показывает, что есть
 * сейчас. Пока прогон идёт (решает сервер по очереди задач), отчёт опрашивается сам
 * (`useRun`), ход — строкой `SegmentProgress`, исход задачи — строкой `JobLine`. Плитки
 * сходятся и во время судьи: «ещё не судили» — своим числом.
 *
 * **Человек решает «на проверку»** — на её вкладке в окне сайтов: «Принять» — сайт пойдёт к поиску
 * адреса продолжением прогона, «Отказ» — отсечён. **«Судья отказал бы»** (вариант (b), 20.10.2026) —
 * своя плитка без цвета внимания: решать по этим сайтам не обязательно, а «Принять» на их вкладке —
 * единственный путь к лиду; плитка — у прогона в наблюдении или когда такие сайты есть.
 * Продолжение — кнопкой: поиск адреса стоит денег (Hunter), и запускает его человек с правом «run»;
 * пока прогон идёт, кнопки нет — принятым адреса ищет он сам. Число у «Найти адреса принятым» —
 * условием прохода (сервер): не искавшиеся, с наступившим сроком повтора и лиды из адреса, что уже
 * есть на домене.
 *
 * **Что ждёт повтора — строками под лидами** (`SegmentWaits`, разбор прогона сбора 20.10.2026):
 * почему словами, когда повтор пойдёт сам и «Повторить сейчас».
 */

import {
  Alert,
  Badge,
  Button,
  Group,
  List,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { Link } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { formatNumber } from '../format';
import { JobLine, jobRestarted } from '../jobs/JobLine';
import { notify } from '../notices';
import { Unfold } from '../runs/Unfold';
import { resumeRun } from './segmentApi';
import type { SegmentCount, SegmentRunDetail } from './segmentApi';
import {
  knownSites,
  leadsLine,
  MODE_WORDS,
  runFacts,
  runJobLine,
  SEGMENT_QUERY_KEY,
  useRun,
  useRunSettled,
} from './segmentData';
import { RunKeywords } from './SegmentRunKeywords';
import { SegmentProgress } from './SegmentProgress';
import { SegmentScopeLine } from './SegmentScopeLine';
import { SegmentSitesWindow } from './SegmentSites';
import { RejudgeStatus, useRejudgeWatch } from './SegmentSitesRejudge';
import type { SiteTab } from './SegmentSitesApi';
import { useSitesWindow } from './SegmentSitesView';
import { REFUSED_TILE } from './SegmentSitesWords';
import { SpendWindow } from './SegmentSpend';
import { SegmentWaits } from './SegmentWaits';
import { isActive, stageBadge } from './segmentStage';

/** Адрес окна сайтов на вкладке — у ссылок отчёта, которые его открывают. */
type Href = (tab: SiteTab) => string;

/** Плитки: сайты прогона и его полосы — ссылки на свою вкладку окна сайтов (`Metric` с `to`):
 *  число ведёт в тот же список, которым посчитано, — «Сайтов из выдачи» на «Все». */
function Tiles({ detail, href }: { detail: SegmentRunDetail; href: Href }) {
  const { summary } = detail;
  const pending = summary.pending ?? detail.lanes.pending ?? 0;
  const refused = summary.would_reject ?? detail.lanes.would_reject ?? 0;
  // «Судья отказал бы» — у прогона в наблюдении и там, где такие сайты есть (как вкладка окна).
  const withRefused = detail.run.mode === 'shadow' || refused > 0;
  return (
    <SimpleGrid cols={{ base: 2, xs: 4, md: withRefused ? 8 : 7 }} spacing="sm">
      <Metric title="Сайтов из выдачи" value={formatNumber(summary.hosts)} to={href('all')} />
      <Metric title="Принято" value={formatNumber(summary.accepted)} to={href('accept')} />
      <Metric
        title="На проверку"
        value={formatNumber(summary.review)}
        color={summary.review > 0 ? 'yellow' : undefined}
        to={href('review')}
      />
      {withRefused && (
        <Metric title={REFUSED_TILE} value={formatNumber(refused)} to={href('would_reject')} />
      )}
      <Metric title="Отсечено" value={formatNumber(summary.rejected)} to={href('reject')} />
      <Metric title="Ещё не судили" value={formatNumber(pending)} to={href('pending')} />
      <Metric title="Без адреса" value={formatNumber(summary.no_address)} />
      <Metric title="Лидов создано" value={formatNumber(summary.leads)} />
    </SimpleGrid>
  );
}

/** Что ждёт человека — сразу под плитками: «На проверку N — решить» открывает вкладку решений. */
function ReviewLine({ detail, href }: { detail: SegmentRunDetail; href: Href }) {
  const total = detail.summary.review;
  if (total === 0) return null;
  return (
    <Group>
      <Button
        component={Link}
        to={href('review')}
        variant="default"
        className="press"
        aria-haspopup="dialog"
      >
        На проверку {formatNumber(total)} — решить
      </Button>
    </Group>
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

/** Подпись свёрнутой строки — по тому, что в ней есть: обещать пустой список нельзя. */
function whyTitle(detail: SegmentRunDetail): string | null {
  const cut = detail.rejected.length > 0;
  const lost = detail.no_address.length > 0;
  if (cut && lost) return 'Почему отсечены и без адреса';
  if (cut) return 'Почему отсечены';
  return lost ? 'Почему без адреса' : null;
}

/** Причины отсева и «без адреса» — одной свёрнутой строкой, раскрываются по нажатию в два такта
 *  (`runs/Unfold`; свёрнутое не отрисовывается). Не в подсказке по наведению: на телефоне её не
 *  открыть, а прочесть нужно. */
function Why({ detail }: { detail: SegmentRunDetail }) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  const title = whyTitle(detail);
  if (title === null) return null;
  return (
    <Stack gap={6}>
      <Group>
        <Button
          variant="subtle"
          size="compact-sm"
          className="press"
          aria-expanded={open}
          aria-controls={panel}
          rightSection={
            <IconChevronDown
              size={14}
              aria-hidden
              style={{ transform: open ? 'rotate(180deg)' : undefined }}
            />
          }
          onClick={() => setOpen((was) => !was)}
        >
          {title}
        </Button>
      </Group>
      <Unfold open={open}>
        <Stack gap="xs" id={panel}>
          <Counts title="Отсечены — почему" rows={detail.rejected} />
          <Counts title="Без адреса — почему" rows={detail.no_address} />
        </Stack>
      </Unfold>
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
  const sites = useSitesWindow(run.id);
  // Пересуд: ход спрашивается, если он был или его только что поставили (окно сайтов).
  const rejudge = useRejudgeWatch(run.id, detail.report.rejudge !== undefined);
  const total = Object.values(detail.lanes).reduce((sum, count) => sum + count, 0);
  return (
    <Stack gap="sm">
      {/* Кнопки окон — сразу за заголовком, а не в правом краю строки (правило 10.10.2026). */}
      <Group gap="xs" wrap="wrap">
        <Title order={5}>Прогон №{run.id}</Title>
        <StageBadge detail={detail} />
        <Button
          component={Link}
          to={sites.href('all')}
          variant="default"
          size="compact-sm"
          className="press"
          aria-haspopup="dialog"
        >
          Сайты прогона · {formatNumber(total)}
        </Button>
        <SpendWindow detail={detail} />
      </Group>
      <Text size="sm" c="dimmed">
        {runFacts(run)}
      </Text>
      <Text size="sm" c="dimmed">
        {MODE_WORDS[run.mode]}.
      </Text>
      <SegmentScopeLine report={detail.report} />
      <SegmentProgress run={run} />
      {run.job_id !== null && jobLineShown(run) && (
        <JobLine
          jobId={run.job_id}
          describe={runJobLine}
          onFinished={() => void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY })}
        />
      )}
      <Tiles detail={detail} href={sites.href} />
      <ReviewLine detail={detail} href={sites.href} />
      <Known detail={detail} />
      <Text size="sm">{leadsLine(detail)}</Text>
      <SegmentWaits detail={detail} />
      <Resume detail={detail} />
      <RejudgeStatus view={rejudge.view} />
      <Why detail={detail} />
      <RunKeywords detail={detail} />
      <SegmentSitesWindow detail={detail} sites={sites} rejudge={rejudge} />
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
  // Другой прогон — свой отчёт с нуля: окна, пересуд, раскрытое «почему» — его, а не прошлого.
  return <Report key={found.data.run.id} detail={found.data} />;
}
