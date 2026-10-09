/**
 * Таблица доноров для обхода и слова о состоянии обхода — у панели «Обход доноров»
 * (`CrawlPanel`). Вынесены, когда панель стала одной строкой в покое (09.10.2026).
 */

import { Badge, Checkbox, Group, Stack, Table, Text } from '@mantine/core';

import type { CrawlRow, CrawlTarget } from '../api/crawls';
import type { Column } from '../components/ColumnsHead';
import { ColumnsHead } from '../components/ColumnsHead';
import { Seams } from '../components/Seams';
import { formatDate, formatMoney, formatNumber, plural } from '../format';
import { RunReason } from '../runs/RunReason';

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

export function CrawlState({ crawl }: { crawl: CrawlRow | null }) {
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

/** «Цена» — по самой длинной: до 100 000 с копейками и валюта кодом,
 *  «99 999,99 USDT». До 07.10.2026 колонка была на 7rem — под доллары знаком. */
const COLUMNS: Column[] = [
  { title: 'Донор' },
  { title: 'Цена', width: '8.5rem' },
  { title: 'Цена от', width: '8rem' },
  { title: 'Последний обход', width: '17rem' },
  { title: 'Ссылок', width: '7rem' },
];
const PICK_WIDTH = '3.25rem';
/** Уже — прокрутка. Колонки с шириной — 700 px вместе с отметкой; донору
 *  остаётся 176 px, как «Донору» в очереди рекламодателей ниже. До 06.10.2026
 *  здесь было 760, и на телефоне донору доставалось 84 px: домен в строку
 *  не помещался и наезжал на цену. */
const TABLE_MIN_WIDTH = 876;

/** Цена с валютой, как её назвали; ручная — с пометкой под ней. Валюты нет
 *  (цена до 07.10.2026 без неё) — только число: знак доллара был бы догадкой. */
function PriceCell({ donor }: { donor: CrawlTarget }) {
  if (donor.price === null) return <>—</>;
  return (
    <Stack gap={0} align="center">
      <span>{formatMoney(donor.price, donor.currency)}</span>
      {donor.source === 'manual' ? (
        <Text size="xs" c="dimmed">
          вручную
        </Text>
      ) : null}
    </Stack>
  );
}

export function TargetsTable({
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
              <Table.Td>
                <PriceCell donor={donor} />
              </Table.Td>
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
