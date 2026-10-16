/**
 * Ключи прогона в отчёте — таблицей: по ключу на строку, сколько новых для гипотезы сайтов он
 * принёс и сколько из них принято.
 *
 * **Почему таблица, а не чипы** (просьба владельца 16.10.2026: «корректнее отобразить список
 * ключей, а то сейчас не особо понятно»). У прогона в одиннадцать ключей чипы с длинными
 * пометками в той же строке переносились вперемешку — где ключ, а где пометка, не видно; одна и
 * та же пометка «похож на «…»» стояла семь раз; галочки выглядели переключателями, хотя в отчёте
 * нажимать нечего; а главного — сколько сайтов принёс каждый ключ — не было вовсе.
 *
 * **Почти повторы — группой под ключом, на который похожи**, с одной строкой «похожи на «…»:
 * выдача может частично совпасть» (группы считает сервер тем же правилом, что пометку на
 * запуске). Сайты по ключу показывают, что повторы почти ничего не добавили: сайт, который нашли
 * несколько ключей, засчитан первому из них по списку. Прочие пометки (название фирмы) — один раз
 * под таблицей, ключи с одинаковой пометкой — одной строкой; снятые на запуске и отсеянные — там
 * же. Что вышло с покупкой выдачи — значком у ключа: «выдача не пришла» (заплачено, а провайдер
 * не отдал) и «ничего не нашлось» (выдача пуста) — разные исходы, разными словами.
 */

import { Badge, Stack, Table, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { Fragment } from 'react';

import { formatNumber } from '../format';
import { FixedTable } from './FixedTable';
import type { Column } from './FixedTable';
import type { SegmentRunDetail, SegmentRunKeywords } from './segmentApi';
import {
  keyCounts,
  LOST_MARK,
  remarkGroups,
  runHeadLine,
  runRows,
  twinCaption,
} from './segmentKeys';
import type { RunKeyRow } from './segmentKeys';

const KEY: Column = { title: 'Ключ', start: true };
/** Числа — по подписи колонки: она шире любого числа сайтов прогона. */
const COUNTS: Column[] = [
  { title: 'Новых сайтов', width: '7.5rem' },
  { title: 'Принято', width: '6rem' },
];
/** Уже таблица не сжимается — уезжает в прокрутку. На телефоне колонок чисел нет: две колонки
 *  в 13,5 rem оставляли ключу 100 px, и ключ рвался посреди слова (снимок 390 px, 16.10). */
const MIN_WIDTH = 300;
const NARROW = '(max-width: 36em)';

/** Числа ключа — колонками, строкой под ключом (узкий экран) или никак: у сервера старше сайтов
 *  по ключу их нет, и колонка нулей соврала бы. */
type Counts = 'columns' | 'inline' | 'none';
/** Почти повтор — с отступом под своим ключом. */
const INDENT = { paddingInlineStart: 'calc(var(--mantine-spacing-xs) + 1rem)' };

const SITES_WORDS =
  'Новые сайты — которых в гипотезе ещё не было; сайт, найденный несколькими ключами, засчитан ' +
  'первому из них по списку.';
const LOST_WORDS = `«${LOST_MARK}» — заплачено, а провайдер не отдал.`;

function Count({ value }: { value: number }) {
  return (
    <Table.Td>
      {/* Ноль — тише: ключ, который ничего не принёс, виден по нему сразу. */}
      <Text size="sm" {...(value === 0 ? { c: 'dimmed' } : {})}>
        {formatNumber(value)}
      </Text>
    </Table.Td>
  );
}

function KeyRow({ row, counts }: { row: RunKeyRow; counts: Counts }) {
  return (
    <Table.Tr data-twin={row.twin === undefined ? undefined : true}>
      <Table.Td className="cellName wrapCell" style={row.twin === undefined ? undefined : INDENT}>
        <Text span size="sm">
          {row.phrase}
        </Text>
        {/* Между ключом и значками — пробелы, а не отступы: строка читается фразой. */}
        {row.marks.map((mark) => (
          <Fragment key={mark}>
            {' '}
            <Badge
              size="sm"
              variant="light"
              color={mark === LOST_MARK ? 'yellow' : 'gray'}
              style={{ verticalAlign: 'middle' }}
            >
              {mark}
            </Badge>
          </Fragment>
        ))}
        {counts === 'inline' && (
          <Text size="xs" c="dimmed" className="runKeysCounts">
            {keyCounts(row)}
          </Text>
        )}
      </Table.Td>
      {counts === 'columns' && (
        <>
          <Count value={row.sites} />
          <Count value={row.accepted} />
        </>
      )}
    </Table.Tr>
  );
}

/** Строка группы почти повторов — во всю ширину, над первым из них. */
function Caption({ twin, members, span }: { twin: string; members: number; span: number }) {
  return (
    <Table.Tr className="runKeysCaption">
      <Table.Td colSpan={span} style={INDENT} ta="left">
        <Text size="xs" c="dimmed">
          {twinCaption(twin, members)}
        </Text>
      </Table.Td>
    </Table.Tr>
  );
}

function Rows({ rows, counts }: { rows: RunKeyRow[]; counts: Counts }) {
  const span = counts === 'columns' ? 1 + COUNTS.length : 1;
  return (
    <>
      {rows.map((row, at) => {
        const first = row.twin !== undefined && rows[at - 1]?.twin !== row.twin;
        const members = rows.filter((each) => each.twin === row.twin).length;
        return (
          <Fragment key={row.phrase}>
            {first && row.twin !== undefined && (
              <Caption twin={row.twin} members={members} span={span} />
            )}
            <KeyRow row={row} counts={counts} />
          </Fragment>
        );
      })}
    </>
  );
}

/** Под таблицей — что значат числа и значки, прочие пометки, снятые и отсеянные ключи. */
function Notes({ found, rows }: { found: SegmentRunKeywords; rows: RunKeyRow[] }) {
  const lines: string[] = [];
  if (found.sites !== undefined) lines.push(SITES_WORDS);
  if (rows.some((row) => row.marks.includes(LOST_MARK))) lines.push(LOST_WORDS);
  for (const group of remarkGroups(found)) {
    lines.push(`${group.phrases.join(', ')} — ${group.words}.`);
  }
  const excluded = found.excluded ?? [];
  if (excluded.length > 0) {
    const verb = excluded.length === 1 ? 'Снят' : 'Сняты';
    lines.push(`${verb} на запуске, выдачу не покупали: ${excluded.join(', ')}.`);
  }
  for (const [phrase, why] of Object.entries(found.rejected)) {
    lines.push(`Отсеян «${phrase}»: ${why}.`);
  }
  return (
    <Stack gap={2} className="runKeysNotes">
      {lines.map((line) => (
        <Text key={line} size="xs" c="dimmed">
          {line}
        </Text>
      ))}
    </Stack>
  );
}

/** Ключи прогона: шапка с числами, таблица ключей и сайтов по ним, под ней — пометки. */
export function RunKeywords({ detail }: { detail: SegmentRunDetail }) {
  const narrow = useMediaQuery(NARROW);
  const found = detail.spend?.keywords;
  if (found === undefined) return null;
  const rows = runRows(found);
  const counts: Counts = found.sites === undefined ? 'none' : narrow ? 'inline' : 'columns';
  return (
    <Stack gap={6} component="section" aria-label="Ключи прогона">
      <Text size="sm">
        <Text span size="sm" fw={500}>
          Ключи прогона
        </Text>
        <Text span size="sm" c="dimmed">
          {' · '}
          {runHeadLine(found)}
        </Text>
      </Text>
      {rows.length > 0 && (
        <FixedTable
          columns={counts === 'columns' ? [KEY, ...COUNTS] : [KEY]}
          minWidth={MIN_WIDTH}
          className="runKeysTable"
          label="Ключи прогона и сайты по ним"
          verticalSpacing="xs"
          tabularNums
          rows={<Rows rows={rows} counts={counts} />}
        />
      )}
      <Notes found={found} rows={rows} />
    </Stack>
  );
}
