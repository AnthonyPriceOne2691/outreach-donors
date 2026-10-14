/**
 * Расход прогона сбора рядом со сметой на запуске — словами сервера (`sales/segment_spend.py`).
 *
 * Человек решает «запускать ли» по смете и «сколько стоило» — по отчёту: обе колонки в одних
 * статьях и единицах. **Статья — своей единицей**: выдача — доллары, судья — токены и доллары,
 * Hunter — запросы поиска и проверки его тарифа; в деньги вместе — только выдача и модель.
 * Этапа, до которого прогон не дошёл, — «ещё не было», а не ноль. Числа экран называет, а не
 * считает: и факт, и смету пишет сервер. На телефоне строка — карточкой (`DataTable`, правило
 * таблиц 09.10): статья, под ней факт и смета с подписью колонки, без прокрутки вбок.
 */

import { Stack, Table, Text } from '@mantine/core';

import { DataTable } from '../components/DataTable';
import type { Column } from '../components/ColumnsHead';
import type { SegmentRunDetail, SegmentSpendRow } from './segmentApi';

const FACT = 'Факт';
const PROMISED = 'Смета на запуске';
const COLUMNS: Column[] = [
  { title: 'Статья', width: '15rem' },
  { title: FACT },
  { title: PROMISED, width: '15rem' },
];

/** Уже этого таблица не сжимается и уезжает в прокрутку: статья, факт словами и смета. */
export const SPEND_MIN_WIDTH = 640;

function Row({ row }: { row: SegmentSpendRow }) {
  const total = row.item === 'money';
  return (
    <Table.Tr>
      <Table.Td className="cellName wrapCell">
        <Text size="sm" fw={total ? 600 : 500}>
          {row.title}
        </Text>
      </Table.Td>
      <Table.Td className="wrapCell" data-label={FACT}>
        <Text size="sm" fw={total ? 600 : undefined}>
          {row.fact}
        </Text>
      </Table.Td>
      <Table.Td className="wrapCell" data-label={PROMISED}>
        <Text size="sm" c="dimmed">
          {row.promised}
        </Text>
      </Table.Td>
    </Table.Tr>
  );
}

export function SegmentSpend({ detail }: { detail: SegmentRunDetail }) {
  const rows = detail.spend?.rows;
  if (rows === undefined || rows.length === 0) return null;
  return (
    <Stack component="section" gap={4} aria-label="Расход прогона">
      <Text size="sm" fw={500}>
        Расход прогона — факт и смета на запуске
      </Text>
      <DataTable columns={COLUMNS} minWidth={SPEND_MIN_WIDTH} className="segmentSpend">
        {rows.map((row) => (
          <Row key={row.item} row={row} />
        ))}
      </DataTable>
      <Text size="xs" c="dimmed">
        Hunter платится запросами своего тарифа: в деньги не переводится и в сумму не входит.
      </Text>
    </Stack>
  );
}
