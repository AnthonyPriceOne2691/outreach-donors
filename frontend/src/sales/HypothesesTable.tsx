/**
 * Гипотезы — кому и зачем пишем — и сколько у каждой лидов в каждом состоянии.
 *
 * Числа — ссылки на вкладку лидов с тем же фильтром: число на сводке — это
 * вход в список, где с ним работают, и искать рядом фильтр незачем. Ноль —
 * не ссылка: вести в пустую таблицу нечестно.
 *
 * Гипотеза заводится командой, а не с экрана: её описание — коммерческий текст,
 * и живёт он данными в базе; экран его только показывает.
 */

import { Anchor, Stack, Table, Text } from '@mantine/core';
import { Link } from 'react-router-dom';

import type { HypothesisCard, LeadState } from '../api/salesTypes';
import { formatDate, formatNumber } from '../format';
import { FixedTable } from './FixedTable';
import type { Column } from './FixedTable';
import { LEAD_STATE_KEYS, NO_LEAD_FILTERS, writeLeadFilters } from './leadFilters';

const COLUMNS: Column[] = [
  { title: 'Гипотеза', width: '20rem' },
  { title: 'Новые', width: '7rem' },
  { title: 'Готовы', width: '7rem' },
  { title: 'Отклонены', width: '8rem' },
  { title: 'Всего', width: '7rem' },
  { title: 'Заведена', width: '8rem' },
];

export const HYPOTHESES_MIN_WIDTH = 912;

/** Ссылка на лидов под фильтром — адресом экрана лидов, тем же, что пишет он сам. */
function leadsAt(hypothesis: number, state: LeadState | null): string {
  return `/sales?${writeLeadFilters({ ...NO_LEAD_FILTERS, state, hypothesis }).toString()}`;
}

function Count({ value, to }: { value: number; to: string }) {
  if (value === 0) {
    return (
      <Text size="sm" c="dimmed">
        0
      </Text>
    );
  }
  return (
    <Anchor component={Link} to={to} size="sm" fw={500}>
      {formatNumber(value)}
    </Anchor>
  );
}

function HypothesisRow({ row }: { row: HypothesisCard }) {
  return (
    <Table.Tr>
      <Table.Td className="cellName wrapCell">
        <Text size="sm" fw={500}>
          {row.name}
        </Text>
        {row.description !== null && (
          <Text size="xs" c="dimmed">
            {row.description}
          </Text>
        )}
      </Table.Td>
      {LEAD_STATE_KEYS.map((state) => (
        <Table.Td key={state}>
          <Count value={row.leads[state]} to={leadsAt(row.id, state)} />
        </Table.Td>
      ))}
      <Table.Td>
        <Count value={row.total} to={leadsAt(row.id, null)} />
      </Table.Td>
      <Table.Td>
        <Text size="sm">{formatDate(row.created_at)}</Text>
      </Table.Td>
    </Table.Tr>
  );
}

export function HypothesesTable({ rows }: { rows: HypothesisCard[] }) {
  if (rows.length === 0) {
    return (
      <Stack gap={6} py="sm">
        <Text size="sm" fw={500}>
          Гипотез пока нет.
        </Text>
        <Text size="sm" c="dimmed">
          Гипотеза — кому и зачем пишем; заводится командой{' '}
          <code>outreach sales-hypothesis-add</code>, базу под неё грузит мастер загрузки.
        </Text>
      </Stack>
    );
  }
  return (
    <FixedTable
      columns={COLUMNS}
      minWidth={HYPOTHESES_MIN_WIDTH}
      className="hypothesesTable"
      horizontalSpacing="md"
      tabularNums
      rows={rows.map((row) => (
        <HypothesisRow key={row.id} row={row} />
      ))}
    />
  );
}
