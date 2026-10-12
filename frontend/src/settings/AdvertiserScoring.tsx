/**
 * Веса, пороги и «кому не пишем» Этапа 2 — что действует (сверка ТЗ 11.10.2026).
 *
 * **Только показ.** Требование просит «все пороги — в настройках, а не в коде»; у
 * Этапа 2 это настройки окружения (`config/advertisers.py`), а не версии, как пороги
 * доноров выше: пересчёт кандидатов обязан переписать вердикт новыми весами, а версия
 * существует ровно для того, чтобы не переписывать. Поэтому полей здесь нет — есть
 * значение и имя настройки, которой его меняет администратор сервера.
 *
 * **Свёрнуто** той же карточкой, что версии настроек агента (`components/FoldCard`):
 * пороги доноров правят на этом экране, а веса скоринга смотрят, когда разбираются
 * со спорным вердиктом. Запрос уходит при раскрытии — закрытая карточка сервер
 * не спрашивает.
 */

import { Alert, Group, Loader, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { fetchAdvertiserSettings } from '../api/settings';
import type { SettingList, SettingNumber } from '../api/settings';
import { FoldCard } from '../components/FoldCard';
import { formatNumber } from '../format';

function Numbers({ title, rows }: { title: string; rows: SettingNumber[] }) {
  return (
    <Table className="dataTable" verticalSpacing="xs" horizontalSpacing="md" tabularNums>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>{title}</Table.Th>
          <Table.Th>Значение</Table.Th>
          <Table.Th>Настройка</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {rows.map((row) => (
          <Table.Tr key={row.setting}>
            <Table.Td>{row.label}</Table.Td>
            <Table.Td>{formatNumber(row.value)}</Table.Td>
            <Table.Td>
              <Text size="xs" c="dimmed" ff="monospace">
                {row.setting}
              </Text>
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function Lists({ rows }: { rows: SettingList[] }) {
  return (
    <Stack gap="sm">
      {rows.map((row) => (
        <Stack key={row.setting} gap={2}>
          <Group gap="xs">
            <Text size="sm" fw={500}>
              {row.label}
            </Text>
            <Text size="xs" c="dimmed" ff="monospace">
              {row.setting}
            </Text>
          </Group>
          <Text size="sm">{row.values.length > 0 ? row.values.join(', ') : 'пусто'}</Text>
        </Stack>
      ))}
    </Stack>
  );
}

export function AdvertiserScoring() {
  const [open, setOpen] = useState(false);
  const { data, error, isLoading } = useQuery({
    queryKey: ['advertiser-settings'],
    queryFn: fetchAdvertiserSettings,
    enabled: open,
  });

  return (
    <FoldCard
      label="Рекламодатели: скоринг ссылок и «кому не пишем»"
      open={open}
      onToggle={() => setOpen((was) => !was)}
    >
      <Stack gap="md" mt="sm">
        <Text size="sm" c="dimmed">
          Действуют сейчас. Меняются настройкой окружения сервера из правой колонки, а не здесь:
          пересчёт кандидатов переписывает вердикты новыми весами, и версии, как у порогов доноров,
          им не подходят.
        </Text>
        {isLoading && <Loader size="sm" aria-label="Загружаем веса скоринга" />}
        {error && (
          <Alert color="red" title="Веса скоринга не загрузились">
            {refusalOf(error)}
          </Alert>
        )}
        {data !== undefined && (
          <>
            <Numbers title="Балл за признак" rows={data.points} />
            <Numbers title="Порог" rows={data.thresholds} />
            <Lists rows={data.lists} />
          </>
        )}
      </Stack>
    </FoldCard>
  );
}
