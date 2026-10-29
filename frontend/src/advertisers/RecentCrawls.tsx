/**
 * Журнал обходов вкладки «Обход доноров» — таблицей со страницами сервера и с «Переобойти».
 * До 29.10.2026 — «Последние обходы», пять строк под панелью обхода (`CrawlPanel`).
 *
 * **Таблица, а не строки** (замечание Anthony 27.10.2026). Строкой «домен · значок · дата»
 * значок вставал там, где кончился домен, — у каждой строки на своём месте, — а причина под
 * ним раздвигала строку, и дата с числами здоровья уезжали ярусом ниже, от левого края. Теперь
 * колонки как у таблицы доноров: донор слева, значок — в своей колонке, причина и числа — в «i»
 * у значка (`CrawlState compact`): журнал, как и таблица доноров, — строка в одну высоту.
 *
 * **«Переобойти» — у последнего обхода каждого донора на странице, чем бы обход ни кончился**
 * (решение Anthony 28.10.2026; с 27.10 кнопка стояла только у частичного, закрывшегося, не
 * начавшегося и остановленного). Сайт растёт: адреса карты сайта идут от новых к старым, а
 * потолок страниц срезает старые (`docs/CRAWL.md`, «Свежие — первыми»), так что повтор через
 * время первыми возьмёт новые статьи. «Обойдён» часто значит «упёрлись в потолок 1 000
 * страниц», а не «взяли весь сайт», а запрет robots.txt со временем может смениться. Пока
 * переобход идёт — в очереди или на ходу, — кнопки нет: второй незаконченный обход донора
 * сервер не заводит (`crawl/launch.py`). Со списком доноров кнопка не сверяется: вкладка грузит
 * доноров страницами, а донору без свежей цены сервер откажет словами.
 *
 * Механизм — тот же запуск, что «Обойти отмеченных» и кнопка в строке таблицы доноров
 * (`CrawlTable.Again`): новый обход заменяет кандидатов прежнего, а решения человека и
 * спрошенный DR переносит (`crawl/gate.py`).
 */

import { Stack, Table, Text } from '@mantine/core';

import type { CrawlRow, CrawlsView } from '../api/crawls';
import type { Column } from '../components/ColumnsHead';
import { ColumnsHead } from '../components/ColumnsHead';
import { Seams } from '../components/Seams';
import { formatDateTime } from '../format';
import type { StartCrawls } from './CrawlTable';
import { Again, CrawlState, isActive } from './CrawlTable';

/** «Обход» — та же ширина, что «Последний обход» у таблицы доноров: значок с потолком и
 *  причина под ним. «Когда и кто» — по адресу автора в 27 знаков; длиннее — переносится. */
const COLUMNS: Column[] = [
  { title: 'Донор' },
  { title: 'Обход', width: '17rem' },
  { title: 'Когда и кто', width: '14rem' },
];
const AGAIN_WIDTH = '9rem';
/** Уже — прокрутка: колонкам с шириной 31 rem (и 9 под кнопку), донору — 176 px, как
 *  в таблице доноров выше. */
const MIN_WIDTH = { plain: 672, again: 816 } as const;

/** Обходы, у которых стоит «Переобойти»: последний в списке у своего донора (строки — новые
 *  сверху; у старой строки повтор уже есть), законченный — «готов» с любым исходом или
 *  остановлен, а не в очереди и не на ходу (`isActive` — те же статусы, что держат замок
 *  сервера «один незаконченный обход на донора»), — и донор в числе тех, кого можно обойти. */
export function againRows(
  rows: readonly CrawlRow[],
  eligible: ReadonlySet<string> | null,
): Set<number> {
  const seen = new Set<string>();
  const again = new Set<number>();
  for (const row of rows) {
    if (seen.has(row.host)) continue;
    seen.add(row.host);
    if ((eligible === null || eligible.has(row.host)) && !isActive(row)) again.add(row.id);
  }
  return again;
}

function When({ crawl }: { crawl: CrawlRow }) {
  return (
    <Stack gap={0} align="center">
      <span>{formatDateTime(crawl.finished_at ?? crawl.created_at)}</span>
      {crawl.requested_by ? (
        <Text size="xs" c="dimmed" className="cellName">
          {crawl.requested_by}
        </Text>
      ) : null}
    </Stack>
  );
}

/** Обходы страницы журнала, новые сверху. `start` — запуск повтора; `null` — нет права
 *  запуска, и колонки с кнопкой нет. */
export function RecentCrawls({
  view,
  start,
}: {
  view: CrawlsView | undefined;
  start: StartCrawls | null;
}) {
  const rows = view?.rows ?? [];
  if (rows.length === 0) return null;
  return (
    <Table.ScrollContainer
      minWidth={start === null ? MIN_WIDTH.plain : MIN_WIDTH.again}
      type="native"
      className="scrollSlim"
    >
      <Table
        className="dataTable fixedTable"
        layout="fixed"
        tabularNums
        verticalSpacing="xs"
        aria-label="Последние обходы"
      >
        <ColumnsHead columns={COLUMNS} tail={start === null ? null : AGAIN_WIDTH} />
        <Table.Tbody>
          <JournalRows rows={rows} start={start} />
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function JournalRows({ rows, start }: { rows: readonly CrawlRow[]; start: StartCrawls | null }) {
  const again = start === null ? new Set<number>() : againRows(rows, null);
  return rows.map((row) => (
    <Table.Tr key={row.id}>
      <Table.Td className="wrapCell cellName">
        <Seams text={row.host} />
      </Table.Td>
      <Table.Td>
        <CrawlState crawl={row} compact />
      </Table.Td>
      <Table.Td className="wrapCell">
        <When crawl={row} />
      </Table.Td>
      {start === null ? null : (
        <Table.Td>{again.has(row.id) ? <Again host={row.host} start={start} /> : null}</Table.Td>
      )}
    </Table.Tr>
  ));
}
