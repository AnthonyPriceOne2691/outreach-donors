/**
 * Выборка спорных по срезам — балл, признак скоринга, ниша донора (замер спорных, 02.11.2026).
 *
 * Общая доля «не пишем» прячет, где ошибается скоринг: порог по баллу, правка веса признака и
 * судья решают разные срезы, и «3 балла проходят, 2 — нет» видно только разбивкой. Числа — от
 * сервера (`crawl/strata.py`), по одной случайной выборке, как и сама доля.
 *
 * **По кнопке, а не сразу**: разбивку смотрят, когда разбирают замер, а очередь — на каждое
 * решение. Запрос уходит, когда её раскрыли; решение в очереди обновляет и её.
 */

import { Alert, Button, Loader, SimpleGrid, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { fetchDisputedStrata } from '../api/advertisers';
import type { DisputedStrata as Strata, Stratum } from '../api/advertisers';
import { refusalOf } from '../api/client';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { formatNumber, plural } from '../format';

/** Под ключом очереди: решение в ней обновляет и разбивку (`invalidateQueries`). */
export const STRATA_KEY = ['advertisers', 'pending', 'strata'] as const;

const COLUMNS: Column[] = [
  { title: 'Срез' },
  { title: 'В выборке', width: '6rem' },
  { title: 'Пишем', width: '5rem' },
  { title: 'Не пишем', width: '5.5rem' },
];
/** Уже — прокрутка: имя среза и три колонки чисел. */
const TABLE_MIN_WIDTH = 320;

function pointsTitle(key: string): string {
  const points = Number(key);
  return `${key} ${plural(points, 'балл', 'балла', 'баллов')}`;
}

function StrataTable({
  label,
  rows,
  title,
}: {
  label: string;
  rows: Stratum[];
  title: (row: Stratum) => string;
}) {
  return (
    <Stack gap={4}>
      <Text size="sm" fw={500}>
        {label}
      </Text>
      <DataTable
        columns={COLUMNS}
        minWidth={TABLE_MIN_WIDTH}
        cardRow
        label={`Выборка спорных ${label.toLowerCase()}`}
      >
        {rows.map((row) => (
          <Table.Tr key={row.key}>
            <Table.Td className="cellName">{title(row)}</Table.Td>
            <Table.Td data-label="В выборке">{formatNumber(row.total)}</Table.Td>
            <Table.Td data-label="Пишем">{formatNumber(row.write)}</Table.Td>
            <Table.Td data-label="Не пишем">{formatNumber(row.skip)}</Table.Td>
          </Table.Tr>
        ))}
      </DataTable>
    </Stack>
  );
}

function Body({ data }: { data: Strata }) {
  if (data.points.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        В случайной выборке спорных строк пока нет.
      </Text>
    );
  }
  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        Только случайная выборка спорных. «Пишем» и «Не пишем» — решения подряд в порядке выборки,
        как у доли. У строки признаков бывает несколько, и она входит в каждый: суммы по признакам
        больше числа строк.
      </Text>
      <SimpleGrid cols={{ base: 1, lg: 3 }} spacing="md">
        <StrataTable label="По баллу" rows={data.points} title={(row) => pointsTitle(row.key)} />
        <StrataTable label="По признаку" rows={data.features} title={(row) => row.title} />
        <StrataTable label="По нише донора" rows={data.niches} title={(row) => row.title} />
      </SimpleGrid>
    </Stack>
  );
}

export function DisputedStrata() {
  const [opened, setOpened] = useState(false);
  const { data, error, isLoading } = useQuery({
    queryKey: STRATA_KEY,
    queryFn: fetchDisputedStrata,
    enabled: opened,
  });
  return (
    <Stack gap="sm">
      <Button
        variant="subtle"
        size="compact-sm"
        className="press"
        style={{ alignSelf: 'flex-start' }}
        aria-expanded={opened}
        onClick={() => setOpened((now) => !now)}
      >
        {opened ? 'Скрыть разбивку выборки' : 'Разбивка выборки спорных'}
      </Button>
      {!opened ? null : isLoading ? (
        <Loader size="sm" aria-label="Загружаем разбивку выборки" />
      ) : error ? (
        <Alert color="red" title="Разбивка не загрузилась">
          {refusalOf(error)}
        </Alert>
      ) : data !== undefined ? (
        <Body data={data} />
      ) : null}
    </Stack>
  );
}
