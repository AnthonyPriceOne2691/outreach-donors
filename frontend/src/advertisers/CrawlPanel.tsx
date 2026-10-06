/**
 * Обход доноров — начало Этапа 2 на экране.
 *
 * До этой панели обход шёл только из консоли (`outreach crawl … --save`),
 * а рекламодатели появлялись на экране без ответа на вопрос «откуда».
 *
 * **Обходят только доноров со свежей ценой.** Оффер рекламодателю строится
 * на «мы дешевле», и дешевле чего — знает только цена донора. Кого нельзя —
 * числами над таблицей и словами сервера, что делать.
 *
 * **Ход — «340 из 1000», а не крутилка.** Обход идёт до получаса; пока он
 * идёт, панель спрашивает сервер сама, и по числу страниц видно, что он жив.
 * Остановленный разбором зависших — красным и с причиной за значком «!».
 *
 * **Кандидаты считаются сами** после обхода (задача пересчёта): когда идущих
 * не осталось, очередь рекламодателей ниже перечитывается.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { refusalOf } from '../api/client';
import type { CrawlLaunch, CrawlRow, CrawlsView, CrawlTarget, CrawlTargets } from '../api/crawls';
import { fetchCrawlTargets, fetchCrawls, startCrawls } from '../api/crawls';
import { useSession } from '../auth/AuthProvider';
import { formatDate, formatDateTime, formatNumber, formatUsd, plural } from '../format';
import type { Column } from '../components/ColumnsHead';
import { ColumnsHead } from '../components/ColumnsHead';
import { Seams } from '../components/Seams';
import { RunReason } from '../runs/RunReason';

export const CRAWL_TARGETS_KEY = ['crawl-targets'] as const;
export const CRAWLS_KEY = ['crawls'] as const;

/** Пока обход идёт, сервер спрашивается раз в десять секунд: страница
 *  донора — около полутора секунд, за десять их набегает пачка. */
const POLL_MS = 10_000;

const ACTIVE = new Set(['queued', 'running']);

export function isActive(crawl: CrawlRow | null): boolean {
  return crawl !== null && ACTIVE.has(crawl.status);
}

/** Что с обходом — словами и цветом. */
export function crawlState(crawl: CrawlRow): { label: string; color: string } {
  const pages = `${formatNumber(crawl.pages)} ${plural(crawl.pages, 'страница', 'страницы', 'страниц')}`;
  if (crawl.status === 'queued') return { label: 'в очереди', color: 'gray' };
  if (crawl.status === 'running') {
    return {
      label: `идёт: ${formatNumber(crawl.pages)} из ${formatNumber(crawl.max_pages)}`,
      color: 'blue',
    };
  }
  if (crawl.status === 'stopped') return { label: 'остановлен — смотрит человек', color: 'red' };
  switch (crawl.outcome) {
    case 'ok':
      return { label: `обойдён: ${pages}`, color: 'green' };
    case 'partial':
      return { label: `частично: ${pages}`, color: 'yellow' };
    case 'forbidden':
      return { label: 'robots.txt запрещает', color: 'gray' };
    case 'blocked':
      return { label: 'закрылся от нас', color: 'red' };
    default:
      return { label: 'не открылся', color: 'red' };
  }
}

function CrawlState({ crawl }: { crawl: CrawlRow | null }) {
  if (crawl === null) {
    return (
      <Text size="sm" c="dimmed">
        не обходили
      </Text>
    );
  }
  const state = crawlState(crawl);
  return (
    <Group gap={6} justify="center" wrap="nowrap">
      <Badge variant="light" color={state.color}>
        {state.label}
      </Badge>
      {crawl.reason ? (
        <RunReason
          reason={crawl.reason}
          status={crawl.status === 'stopped' ? 'stopped' : 'running'}
          subject="обходов"
        />
      ) : null}
    </Group>
  );
}

/** Что вышло из нажатия — одной строкой, с причинами по каждому непоставленному. */
export function launchSummary(done: CrawlLaunch): string {
  const parts = [`Поставлено: ${formatNumber(Object.keys(done.queued).length)}.`];
  if (done.busy.length > 0) parts.push(`Уже идут: ${done.busy.join(', ')}.`);
  for (const [host, why] of Object.entries(done.refused)) parts.push(`${host}: ${why}.`);
  if (done.failed.length > 0) {
    parts.push(`Очередь не ответила: ${done.failed.join(', ')} — попробуйте ещё раз.`);
  }
  return parts.join(' ');
}

function Skipped({
  noPrice,
  stale,
  supplier,
}: {
  noPrice: number;
  stale: number;
  supplier: number;
}) {
  const parts = [
    noPrice > 0 ? `без цены — ${formatNumber(noPrice)}` : null,
    stale > 0 ? `цена протухла — ${formatNumber(stale)}` : null,
    supplier > 0 ? `поставщики — ${formatNumber(supplier)}` : null,
  ].filter((part) => part !== null);
  if (parts.length === 0) return null;
  return (
    <Text size="sm" c="dimmed">
      Не обходим: {parts.join(', ')}.
    </Text>
  );
}

const COLUMNS: Column[] = [
  { title: 'Донор' },
  { title: 'Цена', width: '7rem' },
  { title: 'Цена от', width: '8rem' },
  { title: 'Последний обход', width: '17rem' },
  { title: 'Ссылок', width: '7rem' },
];
const PICK_WIDTH = '3.25rem';
/** Уже — прокрутка. Колонки с шириной — 676 px вместе с отметкой; донору
 *  остаётся 176 px, как «Донору» в очереди рекламодателей ниже. До 06.10.2026
 *  здесь было 760, и на телефоне донору доставалось 84 px: домен в строку
 *  не помещался и наезжал на цену. */
const TABLE_MIN_WIDTH = 852;

function TargetsTable({
  donors,
  picked,
  onPick,
  mayStart,
}: {
  donors: CrawlTarget[];
  picked: Set<string>;
  onPick: (host: string, on: boolean) => void;
  mayStart: boolean;
}) {
  return (
    <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
      {/* Имя донора — слева, как имя в любой таблице, хотя колонка вторая:
          первая — отметка (`pickFirst`). */}
      <Table
        className={mayStart ? 'dataTable fixedTable pickFirst' : 'dataTable fixedTable'}
        layout="fixed"
        tabularNums
        verticalSpacing="sm"
      >
        <ColumnsHead columns={COLUMNS} lead={mayStart ? PICK_WIDTH : null} />
        <Table.Tbody>
          {donors.map((donor) => (
            <Table.Tr key={donor.host}>
              {mayStart ? (
                <Table.Td>
                  <Group justify="center">
                    <Checkbox
                      aria-label={`Обойти ${donor.host}`}
                      checked={picked.has(donor.host)}
                      disabled={isActive(donor.crawl)}
                      onChange={(event) => onPick(donor.host, event.currentTarget.checked)}
                    />
                  </Group>
                </Table.Td>
              ) : null}
              {/* Вторая колонка в строку не переносится (`fixedTable`), и домен
                  длиннее колонки наезжал на соседнюю — имени перенос нужен,
                  как у «Донора» в очереди ниже: по швам, а не где кончилось
                  место. */}
              <Table.Td className="wrapCell cellName">
                <Seams text={donor.host} />
              </Table.Td>
              <Table.Td>{donor.price === null ? '—' : formatUsd(donor.price)}</Table.Td>
              <Table.Td>{formatDate(donor.priced_at)}</Table.Td>
              <Table.Td>
                <CrawlState crawl={donor.crawl} />
              </Table.Td>
              <Table.Td>
                {donor.crawl?.links === null || donor.crawl === null
                  ? '—'
                  : formatNumber(donor.crawl.links)}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function RecentCrawls({ view }: { view: CrawlsView | undefined }) {
  const rows = view?.rows.slice(0, 5) ?? [];
  if (rows.length === 0) return null;
  return (
    <Stack gap={4}>
      <Text size="sm" fw={600}>
        Последние обходы
      </Text>
      {/* На телефоне строка складывается в ярусы — домен, значок, дата, —
          а не ужимает значок до «идё…» (06.10.2026). */}
      {rows.map((row) => (
        <Group key={row.id} gap="xs">
          <Text size="sm" className="cellName">
            <Seams text={row.host} />
          </Text>
          <CrawlState crawl={row} />
          <Text size="xs" c="dimmed">
            {formatDateTime(row.finished_at ?? row.created_at)}
            {row.requested_by ? ` · ${row.requested_by}` : ''}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

/** Доноры и последние обходы — с опросом, пока что-то идёт. Кончился обход —
 *  кандидаты по нему считаются, и очередь рекламодателей ниже перечитывается,
 *  а не ждёт перезагрузки страницы. */
function useCrawlData() {
  const queryClient = useQueryClient();
  const targets = useQuery({
    queryKey: CRAWL_TARGETS_KEY,
    queryFn: fetchCrawlTargets,
    refetchInterval: (query) =>
      query.state.data?.donors.some((donor) => isActive(donor.crawl)) ? POLL_MS : false,
  });
  const recent = useQuery({
    queryKey: CRAWLS_KEY,
    queryFn: fetchCrawls,
    refetchInterval: (query) => ((query.state.data?.active ?? 0) > 0 ? POLL_MS : false),
  });
  const active = recent.data?.active ?? 0;
  const before = useRef(active);
  useEffect(() => {
    if (active < before.current) {
      void queryClient.invalidateQueries({ queryKey: ['advertisers'] });
      void queryClient.invalidateQueries({ queryKey: CRAWL_TARGETS_KEY });
    }
    before.current = active;
  }, [active, queryClient]);
  return { targets, recent };
}

function useStartCrawls(onQueued: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (hosts: string[]) => startCrawls(hosts),
    onSuccess: async (done) => {
      onQueued();
      await queryClient.invalidateQueries({ queryKey: CRAWL_TARGETS_KEY });
      await queryClient.invalidateQueries({ queryKey: CRAWLS_KEY });
      const clean = done.failed.length === 0 && Object.keys(done.refused).length === 0;
      notifications.show({ message: launchSummary(done), color: clean ? 'green' : 'yellow' });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не поставили', message: refusalOf(failure), color: 'red' }),
  });
}

/** Чего панели не хватает: доноры не загрузились, обходчиков нет. */
function Troubles({ error, workers }: { error: unknown; workers: number | null | undefined }) {
  return (
    <>
      {error ? (
        <Alert color="red" title="Доноры не загрузились">
          {refusalOf(error)}
        </Alert>
      ) : null}
      {workers === 0 ? (
        <Alert color="yellow" title="Обходчиков нет">
          Поставленные обходы ждут в очереди, и брать их некому. Поднять: docker compose up -d
          crawler
        </Alert>
      ) : null}
    </>
  );
}

function Lead({ data }: { data: CrawlTargets | undefined }) {
  return (
    <Text size="sm" c="dimmed" maw={720}>
      Этап 2 начинается здесь: обходим доноров с известной и свежей ценой — до{' '}
      {formatNumber(data?.max_pages ?? 1000)} страниц на донора — и ищем, кому они ставят ссылки.
      Кандидаты в рекламодатели считаются сами, когда обход закончится.
    </Text>
  );
}

/** Кого обходим: таблица с отметками или, если некого, — что делать сначала. */
function Targets({
  data,
  picked,
  onPick,
  mayStart,
}: {
  data: CrawlTargets;
  picked: Set<string>;
  onPick: (host: string, on: boolean) => void;
  mayStart: boolean;
}) {
  return (
    <>
      <Skipped noPrice={data.no_price} stale={data.stale_price} supplier={data.supplier} />
      {data.donors.length === 0 ? (
        <Stack gap={4}>
          {data.notes.map((note) => (
            <Text key={note} size="sm">
              {note}
            </Text>
          ))}
        </Stack>
      ) : (
        <TargetsTable donors={data.donors} picked={picked} onPick={onPick} mayStart={mayStart} />
      )}
    </>
  );
}

function togglePicked(was: Set<string>, host: string, on: boolean): Set<string> {
  const next = new Set(was);
  if (on) next.add(host);
  else next.delete(host);
  return next;
}

export function CrawlPanel() {
  const { can } = useSession();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const mayStart = can('run');
  const { targets, recent } = useCrawlData();
  const start = useStartCrawls(() => setPicked(new Set()));
  const data = targets.data;
  const offerStart = mayStart && (data?.donors.length ?? 0) > 0;

  return (
    <Card className="glassPanel crawlPanel" p="xl">
      <Stack gap="sm">
        <Title order={3}>Обход доноров</Title>
        <Lead data={data} />
        {targets.isLoading ? <Loader size="sm" aria-label="Загружаем доноров" /> : null}
        <Troubles error={targets.error} workers={data?.workers} />
        {data ? (
          <Targets
            data={data}
            picked={picked}
            onPick={(host, on) => setPicked((was) => togglePicked(was, host, on))}
            mayStart={mayStart}
          />
        ) : null}
        {offerStart ? (
          <Group>
            <Button
              className="press"
              disabled={picked.size === 0}
              loading={start.isPending}
              onClick={() => start.mutate([...picked])}
            >
              Обойти отмеченных · {formatNumber(picked.size)}
            </Button>
          </Group>
        ) : null}
        <RecentCrawls view={recent.data} />
      </Stack>
    </Card>
  );
}
