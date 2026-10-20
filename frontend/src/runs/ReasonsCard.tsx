/**
 * Кто не дошёл до порогов и почему — словами: исключённые гейтом до Ahrefs и «не
 * проверен» (аудит 20.10.2026). До этого исключённые стояли в отчёте одним числом в пути
 * доменов, а «не проверен» — плиткой без причины: на вопрос «куда делись остальные» отчёт
 * отвечал только про пороги.
 *
 * **«Кому не пишем» — первыми и так и названы**: платформы, соцсети, крупные СМИ,
 * гос. зоны — их не берут ни по каким числам. Слова — сервера (`runs/outcome.py`).
 * Нет ни исключённых, ни «не проверен» — карточки нет: пустая не объясняет ничего.
 */

import { Card, Stack, Table, Text, Title } from '@mantine/core';

import type { NamedCard } from '../api/runReport';
import { ColumnsHead } from '../components/ColumnsHead';
import type { Column } from '../components/ColumnsHead';
import { formatNumber } from '../format';

/** Причине — остаток: «Кому не пишем: справочник или исследования» — самая длинная. */
const COLUMNS: Column[] = [{ title: 'Причина' }, { title: 'Доменов', width: '9rem' }];

function Reasons({ title, rows }: { title: string; rows: NamedCard[] }) {
  return (
    <Stack gap={4}>
      <Text size="sm" fw={500} px="md">
        {title}
      </Text>
      <Table className="dataTable" layout="fixed" verticalSpacing="sm" horizontalSpacing="md">
        <ColumnsHead columns={COLUMNS} />
        <Table.Tbody>
          {rows.map((row) => (
            <Table.Tr key={row.said}>
              <Table.Td>{row.said}</Table.Td>
              <Table.Td>{formatNumber(row.count)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}

interface Props {
  excluded: NamedCard[];
  unchecked: NamedCard[];
}

export function ReasonsCard({ excluded, unchecked }: Props) {
  if (excluded.length === 0 && unchecked.length === 0) return null;
  return (
    <Card className="glass" p="md">
      <Stack gap="md">
        <Stack gap={4} px="md" pt="xs">
          <Title order={5}>Исключены и не проверены</Title>
          <Text size="sm" c="dimmed">
            Исключённых гейт снял до Ahrefs — за них не платили. «Не проверен» — Ahrefs не дал
            данных или их не хватило порогам прогона.
          </Text>
        </Stack>
        {excluded.length > 0 && <Reasons title="Исключено до Ahrefs" rows={excluded} />}
        {unchecked.length > 0 && <Reasons title="Не проверены" rows={unchecked} />}
      </Stack>
    </Card>
  );
}
