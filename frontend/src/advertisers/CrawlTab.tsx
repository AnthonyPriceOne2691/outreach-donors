/**
 * Вкладка «Обход доноров» — начало Этапа 2 на экране (Anthony 29.10.2026: «доноров будут тысячи,
 * панель вываливает простыню — листать два года; компактно, но с фильтрами по статусам»).
 *
 * До 29.10.2026 обход жил панелью над спорными рекламодателями (`CrawlPanel`): до пятисот
 * доноров одной таблицей, счёт — на экране. Теперь:
 *
 * - **страницы, порядок, фильтры и счётчики — на сервере** (`/crawls/donors`, `donor_page.py`), в
 *   адресе (`crawlFilters.ts`): ссылка коллеге открывает тот же список, «назад» работает;
 * - **счётчики по статусу — кнопками-фильтрами** над таблицей (`StatusFacets`), числа — со всеми
 *   условиями, кроме своего;
 * - **строка — в одну высоту** (`CrawlDonorsTable`): причина и числа обхода — в «i» у значка;
 * - **«Обойти отмеченных»** — как было, до полусотни за раз; **«Обойти всех по фильтру»** — всех,
 *   кого можно обойти под условиями, с подтверждением и оценкой времени (`BulkCrawl`);
 * - **«Переобойти» — в строке** у законченного обхода (`CrawlTable.LastCrawl`) и в журнале;
 * - **журнал обходов свёрнут** и листается страницами (`RecentCrawls journal`).
 *
 * Обходят только доноров со свежей ценой — правило сервера (`routes._why_not`); на вкладке видны
 * и доноры с протухшей ценой и без цены — фильтром «Цена», с пометкой у цены и закрытой отметкой.
 * Опрос — только пока что-то идёт или ждёт, лёгкой сводкой (`crawlQueries.useCrawlSummary`).
 */

import { Alert, Button, Card, Group, Modal, Stack, Text, Title } from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import type { CrawlDonorsView, CrawlRules, CrawlSummary } from '../api/crawls';
import { fetchCrawlDonors, fetchCrawls } from '../api/crawls';
import { useSession } from '../auth/AuthProvider';
import { InfoHint } from '../components/InfoHint';
import { PageSwitch } from '../components/PageSwitch';
import type { Emptiness } from '../components/WholeRow';
import { usePagedQuery, useUrlFilters } from '../components/useUrlFilters';
import { formatNumber, formatPercent } from '../format';
import { Unfold } from '../runs/Unfold';
import { BulkCrawl } from './BulkCrawl';
import { CrawlDonorsTable } from './CrawlDonorsTable';
import type { Picks } from './CrawlDonorsTable';
import { CrawlFilterRow, CrawlScope, StatusFacets } from './CrawlFilterBar';
import type { CrawlFilters } from './crawlFilters';
import {
  crawlConditions,
  crawlEmptiness,
  crawlQueryOf,
  DEFAULT_SIZE,
  NO_CRAWL_FILTERS,
  readCrawlFilters,
  statusTotal,
  writeCrawlFilters,
} from './crawlFilters';
import {
  CRAWL_DONORS_KEY,
  CRAWLS_KEY,
  refreshCrawls,
  useCrawlSummary,
  useStartCrawls,
} from './crawlQueries';
import { crawlRule, queueRule } from './crawlWords';
import { ManualDonor } from './ManualDonor';
import { RecentCrawls } from './RecentCrawls';
import { StalePrices } from './StalePrices';

/** Сколько отмеченных ставится одним нажатием — потолок сервера (`schemas.MAX_HOSTS`). */
export const MAX_PICKED = 50;
const MANUAL_FORM_ID = 'manual-donor';
const JOURNAL_ID = 'crawl-journal';

/** Мера здоровья ТЗ словами — числами сервера; сервер старше экрана — без неё. */
function healthRule(data: CrawlRules | undefined): string {
  const window = data?.health_window ?? null;
  const slow = data?.slow_down_share ?? null;
  const stop = data?.stop_share ?? null;
  if (window === null || slow === null || stop === null) return '';
  return (
    ` Под обходом — доля закрытых страниц от всех запросов и мера здоровья ТЗ: доля ` +
    `403/429/5xx/таймаут за последние ${formatNumber(window)} запросов. Больше ` +
    `${formatPercent(slow)} — обход сбавляет темп вдвое, больше ${formatPercent(stop)} — ` +
    'встаёт на паузу.'
  );
}

/** Что вкладка делает — в «i» у заголовка, а не абзацем над таблицей. */
export function about(data: CrawlRules | undefined): string {
  return (
    'Этап 2 начинается здесь: обходим доноров с известной и свежей ценой и ищем, кому они ' +
    'ставят ссылки. Кандидаты в рекламодатели считаются сами, когда обход закончится.' +
    crawlRule(data) +
    healthRule(data) +
    queueRule(data) +
    ' Счётчики над таблицей — фильтры: нажмите статус, чтобы увидеть только его доноров. ' +
    '«Обойти отмеченных» ставит до 50 доноров за раз, «Обойти всех по фильтру» — всех, кого ' +
    'можно обойти под условиями. «Переобойти» — у каждого донора, чей обход закончен: сайт ' +
    'растёт, свежие адреса карты идут первыми, и повтор через время возьмёт новые статьи. Новый ' +
    'обход заменит найденное прежним, а решения по спорным и спрошенный DR перенесёт.'
  );
}

/** Чего вкладке не хватает: доноры не загрузились — в таблице; обходчиков нет — здесь. */
function NoCrawlers({ workers }: { workers: number | null | undefined }) {
  if (workers !== 0) return null;
  return (
    <Alert color="yellow" title="Обходы некому взять">
      Поставленные обходы ждут в очереди: служба обхода не запущена. Запустить её может
      администратор сервиса — сообщите ему, и обходы пойдут сами, по очереди.
    </Alert>
  );
}

/** Что с обходом сейчас — у заголовка: идёт, ждёт, сколько можно обойти. */
function Facts({ summary }: { summary: CrawlSummary | undefined }) {
  if (summary === undefined) return null;
  const { running, queued } = summary.counts.status;
  return (
    <Group gap="xs" align="center">
      {running > 0 ? <Text size="sm">идёт: {formatNumber(running)}</Text> : null}
      {queued > 0 ? (
        <Text size="sm" c="dimmed">
          в очереди: {formatNumber(queued)}
        </Text>
      ) : null}
      <Text size="sm" c="dimmed">
        {summary.startable > 0
          ? `можно обойти: ${formatNumber(summary.startable)}`
          : 'обходить сейчас некого'}
      </Text>
    </Group>
  );
}

function Head({
  data,
  summary,
  onEnter,
}: {
  data: CrawlDonorsView | undefined;
  summary: CrawlSummary | undefined;
  /** Завести донора вручную; `null` — нет права запуска. */
  onEnter: (() => void) | null;
}) {
  return (
    <Group justify="space-between" align="center" gap="sm">
      <Group gap="sm" align="center">
        <Title order={3}>Обход доноров</Title>
        <InfoHint name="Как устроен обход доноров" width={360}>
          {about(data)}
        </InfoHint>
        <Facts summary={summary} />
      </Group>
      {/* Место под «Загрузить списком» (загрузка доноров файлом) — рядом с ручным донором. */}
      {onEnter !== null ? (
        <Button variant="default" className="press" aria-haspopup="dialog" onClick={onEnter}>
          Завести донора вручную
        </Button>
      ) : null}
    </Group>
  );
}

/** Отмеченные и что с ними сделать: обойти, снять; рядом — «Обойти всех по фильтру». */
function Actions({
  data,
  filters,
  picks,
  start,
}: {
  data: CrawlDonorsView | undefined;
  filters: CrawlFilters;
  picks: Picks & { clear: () => void };
  start: ReturnType<typeof useStartCrawls>;
}) {
  const count = picks.picked.size;
  const over = count > MAX_PICKED;
  return (
    <Group gap="xs" align="center">
      <Button
        className="press"
        disabled={count === 0 || over}
        loading={start.isPending}
        onClick={() => start.mutate([...picks.picked])}
      >
        Обойти отмеченных · {formatNumber(count)}
      </Button>
      {data ? (
        <BulkCrawl
          query={crawlQueryOf(filters)}
          startable={data.startable}
          most={data.bulk_max}
          workers={data.workers}
          typicalSec={data.typical_crawl_sec}
          conditions={crawlConditions(filters)}
        />
      ) : null}
      {count > 0 ? (
        <Button variant="subtle" className="press" onClick={picks.clear}>
          Снять отметки
        </Button>
      ) : null}
      {over ? (
        <Text size="sm" c="yellow">
          За раз — до {formatNumber(MAX_PICKED)}; для многих — «Обойти всех по фильтру».
        </Text>
      ) : null}
    </Group>
  );
}

/** Журнал обходов: свёрнут, листается страницами сервера; кнопка повтора — у последнего обхода
 *  донора на странице. */
function Journal({ again }: { again: ReturnType<typeof useStartCrawls> | null }) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(1);
  const recent = useQuery({
    queryKey: [...CRAWLS_KEY, page],
    queryFn: () => fetchCrawls(page),
    enabled: open,
    placeholderData: keepPreviousData,
  });
  const total = recent.data?.total ?? null;
  const pages = total === null ? 1 : Math.max(1, Math.ceil(total / 20));
  return (
    <Stack gap="xs">
      <Group>
        <Button
          variant="subtle"
          className="press"
          aria-expanded={open}
          aria-controls={JOURNAL_ID}
          rightSection={
            <IconChevronDown size={16} style={{ transform: open ? 'rotate(180deg)' : 'none' }} />
          }
          onClick={() => setOpen(!open)}
        >
          Журнал обходов{total !== null ? ` · ${formatNumber(total)}` : ''}
        </Button>
      </Group>
      <Unfold open={open}>
        <Stack gap="xs" id={JOURNAL_ID}>
          <RecentCrawls view={recent.data} start={again} />
          <PageSwitch
            label="Страницы журнала обходов"
            page={page}
            pages={pages}
            onChange={setPage}
          />
        </Stack>
      </Unfold>
    </Stack>
  );
}

/** Отметки строк: свои у вкладки, переживают страницу и смену условий, в адрес не пишутся
 *  (`UI_RULES`: ссылка коллеге не несёт чужих отметок). */
function usePicks(): Picks & { clear: () => void } {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  return {
    picked,
    onPick: (host, on) =>
      setPicked((was) => {
        const next = new Set(was);
        if (on) next.add(host);
        else next.delete(host);
        return next;
      }),
    onPickAll: (hosts, on) =>
      setPicked((was) => {
        const next = new Set(was);
        for (const host of hosts) {
          if (on) next.add(host);
          else next.delete(host);
        }
        return next;
      }),
    clear: () => setPicked(new Set()),
  };
}

/** Страница доноров с сервера по условиям адреса. */
function useDonorPage(filters: CrawlFilters, turn: (page: number, replace?: boolean) => void) {
  return usePagedQuery(
    [...CRAWL_DONORS_KEY, writeCrawlFilters(filters).toString()],
    () =>
      fetchCrawlDonors(
        crawlQueryOf(filters),
        filters.page,
        filters.size === DEFAULT_SIZE ? undefined : filters.size,
      ),
    filters.page,
    turn,
  );
}

/** Почему пусто — словами. Сколько доноров без условий — из сводки: `total` страницы — уже под
 *  условиями, и пусто под фильтром читалось бы «обходить некого». */
function emptinessOf(
  filters: CrawlFilters,
  data: CrawlDonorsView | undefined,
  summary: CrawlSummary | undefined,
): Emptiness | null {
  if (data === undefined || data.rows.length > 0) return null;
  return crawlEmptiness(filters, summary ? statusTotal(summary.counts) : data.total);
}

/** Над таблицей — чего не хватает (обходчиков нет) и протухшие цены с «Перезапросить цену». */
function Notices({
  data,
  summary,
}: {
  data: CrawlDonorsView | undefined;
  summary: CrawlSummary | undefined;
}) {
  const { can } = useSession();
  const queryClient = useQueryClient();
  return (
    <>
      <NoCrawlers workers={data?.workers ?? summary?.workers} />
      {summary ? (
        <StalePrices
          count={summary.counts.price.stale}
          maySend={can('send')}
          onAsked={() => void refreshCrawls(queryClient)}
        />
      ) : null}
    </>
  );
}

/** Чем запускать с вкладки; `null` — нет права запуска: ни отметок, ни кнопок. */
interface Runner {
  picks: Picks & { clear: () => void };
  start: ReturnType<typeof useStartCrawls>;
  again: ReturnType<typeof useStartCrawls>;
}

/** Что с ответом страницы: первого ещё нет, едет новый (прежние строки приглушены), отказ. */
function pageState({ query, data, settled }: ReturnType<typeof useDonorPage>) {
  return {
    loading: data === undefined && query.error === null,
    stale: !settled && data !== undefined,
    refusal: query.error ? refusalOf(query.error) : null,
  };
}

/** Доноры страницы: таблица с фильтрами под колонками и переключатель страниц. */
function Donors({
  url,
  page,
  summary,
  runner,
}: {
  url: ReturnType<typeof useUrlFilters<CrawlFilters>>;
  page: ReturnType<typeof useDonorPage>;
  summary: CrawlSummary | undefined;
  runner: Runner | null;
}) {
  const { data, pages } = page;
  return (
    <>
      <CrawlDonorsTable
        rows={data?.rows ?? []}
        picks={runner?.picks ?? null}
        again={runner?.again ?? null}
        filterRow={
          <CrawlFilterRow
            counts={data?.counts}
            filters={url.filters}
            picks={runner !== null}
            onFilter={url.apply}
          />
        }
        {...pageState(page)}
        empty={emptinessOf(url.filters, data, summary)}
        onReset={() => url.reset(NO_CRAWL_FILTERS)}
      />
      <PageSwitch
        label="Страницы доноров"
        page={url.filters.page}
        pages={pages}
        onChange={url.turn}
      />
    </>
  );
}

/** «Завести донора вручную» — окном; заведён — вкладка и список доноров перечитываются. */
function ManualDonorModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  return (
    <Modal opened={opened} onClose={onClose} title="Завести донора вручную" size="lg">
      <ManualDonor
        id={MANUAL_FORM_ID}
        onEntered={() => {
          onClose();
          void refreshCrawls(queryClient);
          void queryClient.invalidateQueries({ queryKey: ['donors'] });
        }}
        onCancel={onClose}
      />
    </Modal>
  );
}

export function CrawlTab() {
  const { can } = useSession();
  const [entering, setEntering] = useState(false);
  const url = useUrlFilters(readCrawlFilters, writeCrawlFilters);
  const page = useDonorPage(url.filters, url.turn);
  const summary = useCrawlSummary();
  const picks = usePicks();
  const start = useStartCrawls(picks.clear);
  const again = useStartCrawls();
  const runner: Runner | null = can('run') ? { picks, start, again } : null;
  const data = page.data;

  return (
    <Card className="glassPanel crawlPanel" p="xl">
      <Stack gap="sm">
        <Head
          data={data}
          summary={summary.data}
          onEnter={runner !== null ? () => setEntering(true) : null}
        />
        <Notices data={data} summary={summary.data} />
        <Group justify="space-between" align="center" gap="sm">
          <StatusFacets counts={data?.counts} filters={url.filters} onFilter={url.apply} />
          <CrawlScope filters={url.filters} onFilter={url.apply} />
        </Group>
        {runner !== null ? (
          <Actions data={data} filters={url.filters} picks={picks} start={start} />
        ) : null}
        <Donors url={url} page={page} summary={summary.data} runner={runner} />
        <Journal again={runner?.again ?? null} />
      </Stack>
      <ManualDonorModal opened={entering} onClose={() => setEntering(false)} />
    </Card>
  );
}
