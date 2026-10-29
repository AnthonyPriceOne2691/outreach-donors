/**
 * Доноры вкладки «Обход доноров» — плотной таблицей (Anthony 29.10.2026: тысячи доноров
 * простынёй не листаются).
 *
 * **Строка — в одну высоту.** Почему обход кончился, место в очереди и числа здоровья — в «i» у
 * значка (`CrawlState compact`), а не столбиком под ним: под значком они растили строку втрое.
 * Шапка закреплена (`DataTable sticky`): у страницы в сотню строк заголовки не уезжают за край.
 *
 * **Отметить можно того, кого можно обойти**: обход не идёт и не ждёт, цена в сроке. Донора с
 * протухшей ценой или без цены сервер всё равно не поставит — у его цены так и написано, а
 * отметка закрыта. «Все на этой странице» отмечает только таких.
 *
 * **До первого ответа — заготовка строк** (`LoadingRows`), а не крутилка над пустой шапкой:
 * таблица встаёт сразу в свою высоту, и ответ не сдвигает экран.
 */

import { Checkbox, Group, Skeleton, Table } from '@mantine/core';
import type { ReactNode } from 'react';

import type { CrawlTarget } from '../api/crawls';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { Seams } from '../components/Seams';
import { NoRows } from '../components/WholeRow';
import type { Emptiness } from '../components/WholeRow';
import { formatDate, formatNumber } from '../format';
import type { StartCrawls } from './CrawlTable';
import { LastCrawl, PriceCell, isActive } from './CrawlTable';

const PICK = 'Отметить';

/** «Цена» — по самой длинной: «99 999,99 USDT» и пометка под ней; «Цена от» — дата и поле
 *  «протухла» под ней. Донору — постоянная ширина (домен переносится по швам), а остаток — у
 *  «Последнего обхода»: значок «обойдён: 1 000 из 1 000 страниц», «i» и «Переобойти» встают в
 *  одну строку, где есть 344 px (1440 — есть), а уже — «Переобойти» уходит под значок. Наоборот
 *  (остаток — донору) на 1280 домен рвался на три строки в каждой строке таблицы (замер
 *  29.10.2026). */
const COLUMNS: Column[] = [
  { title: 'Донор', width: '15rem' },
  { title: 'Цена', width: '8.5rem' },
  { title: 'Цена от', width: '8rem' },
  { title: 'Последний обход' },
  { title: 'Ссылок', width: '5.5rem' },
];
const PICK_COLUMN: Column = { title: PICK, width: '3.25rem' };
/** Строк заготовки до первого ответа — сколько видно без прокрутки. */
const LOADING_ROWS = 6;
/** Уже — прокрутка: колонкам с шириной 40,25rem (644 px), обходу — от 240 px. */
const MIN_WIDTH = 884;

/** Можно ли отметить донора: обход не идёт и не ждёт, цена в сроке. У панели, где в списке
 *  только свежие, `price_state` нет — там решает только обход. */
export function pickable(donor: CrawlTarget): boolean {
  const priced = donor.price !== null && (donor.price_state ?? 'fresh') === 'fresh';
  return priced && !isActive(donor.crawl);
}

export interface Picks {
  picked: Set<string>;
  onPick: (host: string, on: boolean) => void;
  onPickAll: (hosts: string[], on: boolean) => void;
}

/** Флажок «все на этой странице»: только тех, кого можно отметить; отмечена часть —
 *  промежуточное состояние. */
function PickAll({ rows, picks }: { rows: CrawlTarget[]; picks: Picks }) {
  const hosts = rows.filter(pickable).map((donor) => donor.host);
  const on = hosts.filter((host) => picks.picked.has(host)).length;
  const all = hosts.length > 0 && on === hosts.length;
  return (
    <Checkbox
      size="sm"
      aria-label="Отметить всех на этой странице"
      checked={all}
      indeterminate={on > 0 && !all}
      disabled={hosts.length === 0}
      onChange={() => picks.onPickAll(hosts, !all)}
    />
  );
}

/** Почему отметка закрыта — подписью флажка, а не догадкой. */
function pickLabel(donor: CrawlTarget): string {
  if (isActive(donor.crawl)) return `${donor.host}: обход уже идёт или ждёт`;
  if (donor.price === null || donor.price_state === 'none') return `${donor.host}: цены нет`;
  if (donor.price_state === 'stale') return `${donor.host}: цена протухла — сначала перезапрос`;
  return `Обойти ${donor.host}`;
}

function DonorRowCells({
  donor,
  picks,
  again,
}: {
  donor: CrawlTarget;
  picks: Picks | null;
  again: StartCrawls | null;
}) {
  return (
    <Table.Tr>
      {picks !== null ? (
        <Table.Td>
          <Group justify="center">
            <Checkbox
              aria-label={pickLabel(donor)}
              checked={picks.picked.has(donor.host)}
              disabled={!pickable(donor)}
              onChange={(event) => picks.onPick(donor.host, event.currentTarget.checked)}
            />
          </Group>
        </Table.Td>
      ) : null}
      <Table.Td className="wrapCell cellName" data-label="Донор">
        <Seams text={donor.host} />
      </Table.Td>
      <Table.Td data-label="Цена">
        <PriceCell donor={donor} />
      </Table.Td>
      <Table.Td data-label="Цена от">{formatDate(donor.priced_at)}</Table.Td>
      {/* На телефоне — сразу под именем, во всю ширину карточки (`cellDecide`): значок обхода и
          «Переобойти» — то, ради чего строка; цена, дата и ссылки — строкой ниже. */}
      <Table.Td className="cellDecide" data-label="Последний обход">
        <LastCrawl donor={donor} again={again} compact />
      </Table.Td>
      <Table.Td data-label="Ссылок">
        {donor.crawl?.links === null || donor.crawl === null
          ? '—'
          : formatNumber(donor.crawl.links)}
      </Table.Td>
    </Table.Tr>
  );
}

/** Заготовка строк до первого ответа: полоса в каждой ячейке, читалке — «загружается». */
function LoadingRows({ span }: { span: number }) {
  return Array.from({ length: LOADING_ROWS }, (_, row) => (
    <Table.Tr key={row} aria-hidden="true">
      {Array.from({ length: span }, (_, cell) => (
        <Table.Td key={cell}>
          <Skeleton height={14} radius="xl" />
        </Table.Td>
      ))}
    </Table.Tr>
  ));
}

export function CrawlDonorsTable({
  rows,
  picks,
  again,
  filterRow,
  loading,
  stale,
  empty,
  refusal,
  onReset,
}: {
  rows: CrawlTarget[];
  /** Отметки; `null` — нет права запуска: ни отметок, ни «Переобойти». */
  picks: Picks | null;
  again: StartCrawls | null;
  /** Строка фильтров под заголовками (`CrawlFilterRow`). */
  filterRow: ReactNode;
  /** Первого ответа ещё нет. */
  loading: boolean;
  /** Едет новая страница — прежние строки приглушены. */
  stale: boolean;
  empty: Emptiness | null;
  refusal: string | null;
  onReset: () => void;
}) {
  const columns = picks !== null ? [PICK_COLUMN, ...COLUMNS] : COLUMNS;
  return (
    <DataTable
      columns={columns}
      minWidth={MIN_WIDTH}
      className={
        picks !== null ? 'filteredTable pickFirst allCentered' : 'filteredTable allCentered'
      }
      heads={picks !== null ? { [PICK]: <PickAll rows={rows} picks={picks} /> } : undefined}
      head={filterRow}
      stale={stale || loading}
      cardRow
      sticky
      label="Доноры для обхода"
    >
      <NoRows
        span={columns.length}
        refusal={refusal}
        failTitle="Доноры не загрузились"
        empty={empty}
        onReset={onReset}
        wide
      />
      {loading && refusal === null ? <LoadingRows span={columns.length} /> : null}
      {refusal === null
        ? rows.map((donor) => (
            <DonorRowCells key={donor.host} donor={donor} picks={picks} again={again} />
          ))
        : null}
    </DataTable>
  );
}
