/**
 * Общий стоп-лист на вкладке «Стоп-лист» продаж — только виден: отписавшиеся и пожаловавшиеся
 * с любого этапа, записи без этапа и этапа продаж тоже держат продажи
 * (`suppressions.holds_sales` сервера). Снимают их на экране «Стоп-лист»: там снятие решения
 * адресата уходит в журнал с причиной. Строк — новые первыми, не все: сколько всего — числом.
 * Показывается с правом смотреть базу (`view`): без него сервер строк не присылает, и «никого»
 * было бы неправдой.
 */

import { Badge, Button, Group, Stack, Table, Text } from '@mantine/core';
import { Link } from 'react-router-dom';

import { SUPPRESSION_REASON_TITLES } from '../api/labels';
import type { SalesHeldRow, SalesStoplistView } from '../api/salesStoplist';
import { InfoHint } from '../components/InfoHint';
import { Seams } from '../components/Seams';
import { formatDate, formatNumber } from '../format';
import { FixedTable } from './FixedTable';
import type { Column } from './FixedTable';

const GENERAL_COLUMNS: Column[] = [
  { title: 'Кому не пишем' },
  { title: 'Причина', width: '9rem' },
  { title: 'Когда', width: '7.5rem' },
];
const GENERAL_MIN_WIDTH = 560;

function GeneralRow({ row }: { row: SalesHeldRow }) {
  return (
    <Table.Tr>
      <Table.Td className="cellName">
        <Seams text={row.target} address />
      </Table.Td>
      <Table.Td>
        <Badge variant="light" color="gray">
          {SUPPRESSION_REASON_TITLES[row.reason]}
        </Badge>
      </Table.Td>
      <Table.Td>{formatDate(row.created_at)}</Table.Td>
    </Table.Tr>
  );
}

/** Общий стоп-лист, который держит продажи: только виден, снимают его не здесь. Только с правом
 *  `view` — проверяет вкладка. */
export function StoplistGeneral({ view }: { view: SalesStoplistView }) {
  const shown = view.general.length;
  return (
    <Stack gap="xs">
      <Group gap={4} wrap="nowrap" align="flex-start" px="md">
        <Text size="sm" fw={500}>
          Общий стоп-лист тоже держит продажи — {formatNumber(view.general_total)}
        </Text>
        <InfoHint name="Что держит продажи из общего стоп-листа" width={340}>
          Отписавшиеся и пожаловавшиеся с любого этапа, записи без этапа и этапа продаж. Снимают их
          на экране «Стоп-лист»: там снятие решения адресата уходит в журнал с причиной.
        </InfoHint>
      </Group>
      {shown === 0 ? (
        <Text size="sm" c="dimmed" px="md">
          Никого: общий стоп-лист продажи не держит.
        </Text>
      ) : (
        <FixedTable
          columns={GENERAL_COLUMNS}
          minWidth={GENERAL_MIN_WIDTH}
          className="stoplistGeneral"
          label="Общий стоп-лист, который держит продажи"
          horizontalSpacing="md"
          rows={view.general.map((row) => (
            <GeneralRow key={row.id} row={row} />
          ))}
        />
      )}
      <Group gap="sm" px="md">
        {view.general_total > shown && (
          <Text size="xs" c="dimmed">
            Показаны {formatNumber(shown)} новых из {formatNumber(view.general_total)}.
          </Text>
        )}
        <Button component={Link} to="/suppressions" variant="subtle" size="compact-sm">
          К общему стоп-листу
        </Button>
      </Group>
    </Stack>
  );
}
