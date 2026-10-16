/**
 * Расход прогона сбора рядом со сметой на запуске — словами сервера (`sales/segment_spend.py`).
 *
 * Человек решает «запускать ли» по смете и «сколько стоило» — по отчёту: обе колонки в одних
 * статьях и единицах. **Статья — своей единицей**: выдача — доллары, судья — токены и доллары,
 * Hunter — запросы поиска и проверки его тарифа; в деньги вместе — только выдача и модель.
 * Этапа, до которого прогон не дошёл, — «ещё не было», а не ноль. Числа экран называет, а не
 * считает: и факт, и смету пишет сервер. На телефоне строка — карточкой (`DataTable`, правило
 * таблиц 09.10): статья, под ней факт и смета с подписью колонки, без прокрутки вбок.
 *
 * **В окне «Расход и смета»**, кнопкой у заголовка прогона (15.10.2026): отчёт читается за один-два
 * экрана, а таблица статей и смета словами — по нажатию. В окне — расход рядом со сметой и смета на
 * запуске строками сервера, как были в отчёте. Ни того, ни другого у прогона нет — нет и кнопки.
 */

import { Button, Stack, Table, Text } from '@mantine/core';
import { useState } from 'react';

import { DataTable } from '../components/DataTable';
import type { Column } from '../components/ColumnsHead';
import type { SegmentRunDetail, SegmentSpendRow } from './segmentApi';
import { ReportWindow } from './SegmentReportWindow';

const FACT = 'Факт';
const PROMISED = 'Смета на запуске';
const COLUMNS: Column[] = [
  { title: 'Статья', width: '15rem' },
  { title: FACT },
  { title: PROMISED, width: '15rem' },
];

/** Уже этого таблица не сжимается и уезжает в прокрутку: статья, факт словами и смета. */
export const SPEND_MIN_WIDTH = 640;

/** Окно расхода на широком экране: факт судьи («токенов 81 412, из них вывод 11 310, ≈ $0,04»)
 *  встаёт в строку — в окне 780 px он рвался посреди числа (у сервера разряды — пробелом). */
const WINDOW_SIZE = '60rem';

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

/** Смета на запуске — строками сервера, как её видел человек перед «Запустить прогон». */
function Promised({ lines }: { lines: string[] }) {
  if (lines.length === 0) return null;
  return (
    <Stack component="section" gap={2} aria-label="Смета на запуске">
      <Text size="sm" fw={500}>
        Смета на запуске
      </Text>
      {lines.map((line) => (
        <Text key={line} size="sm" c="dimmed">
          {line}
        </Text>
      ))}
    </Stack>
  );
}

/** «Расход и смета» у заголовка прогона — и окно: расход статьями и смета на запуске. */
export function SpendWindow({ detail }: { detail: SegmentRunDetail }) {
  const [open, setOpen] = useState(false);
  const rows = detail.spend?.rows ?? [];
  if (rows.length === 0 && detail.estimate_lines.length === 0) return null;
  return (
    <>
      <Button
        variant="default"
        size="compact-sm"
        className="press"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        Расход и смета
      </Button>
      <ReportWindow
        opened={open}
        onClose={() => setOpen(false)}
        title={`Расход и смета прогона №${detail.run.id}`}
        size={WINDOW_SIZE}
      >
        <Stack gap="md">
          <SegmentSpend detail={detail} />
          <Promised lines={detail.estimate_lines} />
        </Stack>
      </ReportWindow>
    </>
  );
}
