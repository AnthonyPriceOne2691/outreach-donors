/**
 * Таблица очереди проверки — спорные или «куплено»: кому ссылается донор, балл и за
 * что он, найденная ссылка, а у кого есть право — «Пишем» и «Не пишем».
 *
 * Своим файлом, как таблицы списка (`AdvertiserListTable`) и обхода (`CrawlTable`):
 * на экране рекламодателей три вида, и очередь — часть одного из них. Зачем проверка
 * и как устроен её вид — в `AdvertisersPage.tsx`.
 */

import { Anchor, Badge, Button, Group, Stack, Table, Text } from '@mantine/core';

import type { CandidateCard } from '../api/types';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { Seams } from '../components/Seams';
import { formatDate, formatNumber, plural } from '../format';
import { LinkAnchor } from './LinkAnchor';

/** Как называется каждый исход скоринга на человеческом языке. */
export const VERDICTS: Record<CandidateCard['verdict'], { label: string; color: string }> = {
  bought: { label: 'куплена', color: 'green' },
  pending: { label: 'спорно', color: 'yellow' },
  skipped: { label: 'мимо', color: 'gray' },
  blocked: { label: 'кому не пишем', color: 'gray' },
};

/** Колонки слева направо. **Остаток — «За что»**: в ней причины скоринга, по строке
 *  на причину, и в 15rem они переносились, а «+2» вставало на свою строку — строка
 *  таблицы росла до 130–160 px, пока «Кому ссылается» держала ~300 px под короткий
 *  домен (аудит экранов 09.10.2026). Домену — 13rem, он переносится по швам; «Ссылка» —
 *  текст; остальные — по самому длинному шрифтом экрана (25.09.2026): кнопки «Пишем» и
 *  «Не пишем» с зазором — около 154 px. Плюс 32 px полей ячейки и запас. */
const COLUMNS: Column[] = [
  { title: 'Кому ссылается', width: '13rem' },
  { title: 'Донор', width: '11rem' },
  { title: 'Балл', width: '4.75rem' },
  { title: 'За что' },
  { title: 'Ссылка', width: '12rem' },
];
const ACTIONS_WIDTH = '12.5rem';
const TABLE_MIN_WIDTH = 1080;

/** «Пишем» и «Не пишем» — у кого есть право решать. */
function Decide({
  row,
  busy,
  onDecide,
}: {
  row: CandidateCard;
  /** «Пишем» этой строки уходит на сервер. */
  busy: boolean;
  onDecide: (row: CandidateCard, confirmed: boolean) => void;
}) {
  return (
    <Table.Td className="cellDecide">
      {/* Обе кнопки — одного веса: экран меряет ошибки скоринга, и залитая «Пишем»
          в каждой строке подталкивала соглашаться с ним (аудит экранов 09.10.2026). */}
      <Group gap="xs" justify="center" wrap="nowrap">
        <Button
          size="compact-sm"
          variant="light"
          className="press"
          loading={busy}
          onClick={() => onDecide(row, true)}
        >
          Пишем
        </Button>
        <Button
          size="compact-sm"
          variant="default"
          className="press"
          onClick={() => onDecide(row, false)}
        >
          Не пишем
        </Button>
      </Group>
    </Table.Td>
  );
}

/** Ячейки строки до кнопок решения. */
function Cells({ row }: { row: CandidateCard }) {
  return (
    <>
      <Table.Td>
        {/* Имя — чернилами, как во всех таблицах: бирюзовая ссылка на бирюзовом
            углу полотна не держит норму. */}
        <Anchor
          href={`https://${row.target_root}`}
          target="_blank"
          rel="noreferrer"
          c="var(--ink)"
          fw={500}
          underline="hover"
          className="cellName"
        >
          <Seams text={row.target_root} />
        </Anchor>
        {row.confirmed !== null ? (
          <Text size="xs" c="dimmed" className="cellName">
            {row.confirmed ? 'пишем' : 'не пишем'} — {row.decided_by}
          </Text>
        ) : null}
      </Table.Td>
      <Table.Td className="wrapCell cellName" data-label="Донор">
        <Seams text={row.donor_host} />
      </Table.Td>
      <Table.Td data-label="Балл">
        <Badge variant="light" color={VERDICTS[row.verdict].color}>
          {row.points}
        </Badge>
      </Table.Td>
      {/* Причины — строками по центру, как всё в колонке, без маркеров списка:
          маркеры стояли у левого края, а текст посередине, и строки читались
          оторванными от них. */}
      <Table.Td className="wrapCell" data-label="За что">
        <Stack gap={2}>
          {row.reasons.map((reason) => (
            <Text key={reason} size="xs">
              {reason}
            </Text>
          ))}
          <Text size="xs" c="dimmed">
            {formatNumber(row.links)} {plural(row.links, 'ссылка', 'ссылки', 'ссылок')} с{' '}
            {formatNumber(row.pages)} {plural(row.pages, 'страницы', 'страниц', 'страниц')}
          </Text>
        </Stack>
      </Table.Td>
      <Table.Td className="wrapCell" data-label="Ссылка">
        {row.best_page_url ? (
          <Anchor href={row.best_page_url} target="_blank" rel="noreferrer" size="sm">
            страница
          </Anchor>
        ) : (
          '—'
        )}
        {row.best_anchor ? <LinkAnchor anchor={row.best_anchor} /> : null}
        {row.best_published ? (
          <Text size="xs" c="dimmed">
            статья от {formatDate(row.best_published)}
          </Text>
        ) : null}
      </Table.Td>
    </>
  );
}

interface Props {
  rows: CandidateCard[];
  /** Право решать: без него нет ни кнопок, ни их колонки. */
  mayDecide: boolean;
  /** Строка, чьё «Пишем» сейчас уходит на сервер. */
  deciding: number | null;
  onDecide: (row: CandidateCard, confirmed: boolean) => void;
}

export function CandidatesTable({ rows, mayDecide, deciding, onDecide }: Props) {
  return (
    <DataTable columns={COLUMNS} minWidth={TABLE_MIN_WIDTH} tail={mayDecide ? ACTIONS_WIDTH : null}>
      {rows.map((row) => (
        <Table.Tr key={row.id}>
          <Cells row={row} />
          {mayDecide ? <Decide row={row} busy={deciding === row.id} onDecide={onDecide} /> : null}
        </Table.Tr>
      ))}
    </DataTable>
  );
}
