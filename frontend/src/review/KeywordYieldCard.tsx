/**
 * Что дали ключи прогона: по каким запросам нашлись доноры, а какие
 * приносят только отказы или ничего.
 *
 * **Свёрнуто по умолчанию.** Главная работа экрана — решения по доменам;
 * отдача ключей нужна, чтобы собрать следующий прогон, и открывается,
 * когда до этого дошло.
 *
 * **«Не знаем» — не таблица нулей.** Прогоны до 23.09.2026 не хранили,
 * какой ключ что нашёл, и для них это сказано словами.
 */

import { Badge, Button, Group, Stack, Table, Text } from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useId, useState } from 'react';

import type { KeywordYield } from '../api/types';

interface Props {
  keywords: KeywordYield[] | null;
}

function Summary({ rows }: { rows: KeywordYield[] }) {
  const gave = rows.filter((row) => row.accepted > 0).length;
  const nothing = rows.filter((row) => row.found === 0).length;
  const onlyRejected = rows.filter(
    (row) => row.accepted === 0 && row.pending === 0 && row.rejected > 0,
  ).length;
  return (
    <Text size="sm" c="dimmed">
      Принятых дали {gave} из {rows.length} ключей
      {onlyRejected > 0 && `, только отказы — ${onlyRejected}`}
      {nothing > 0 && `, ничего не нашли — ${nothing}`}.
    </Text>
  );
}

export function KeywordYieldCard({ keywords }: Props) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();

  if (keywords === null) {
    return (
      <Text size="sm" c="dimmed">
        Какой ключ что нашёл, этот прогон не хранит — он запущен до того, как это стали записывать.
        Отдачу его ключей не посчитать.
      </Text>
    );
  }

  return (
    <Stack gap="sm">
      <Group gap="sm">
        <Button
          variant="subtle"
          className="press"
          aria-expanded={open}
          aria-controls={bodyId}
          rightSection={
            <IconChevronDown
              size={16}
              style={{
                transform: open ? 'rotate(180deg)' : 'none',
                transition: 'transform 200ms cubic-bezier(0.32, 0.72, 0, 1)',
              }}
            />
          }
          onClick={() => setOpen((was) => !was)}
        >
          Что дали ключи прогона
        </Button>
      </Group>
      <Summary rows={keywords} />
      {open ? (
        <Table.ScrollContainer minWidth={640} id={bodyId}>
          <Table verticalSpacing="xs" className="dataTable">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Ключ</Table.Th>
                <Table.Th>Нашёл доменов</Table.Th>
                <Table.Th>Дошли до рассмотрения</Table.Th>
                <Table.Th>Принято</Table.Th>
                <Table.Th>Отклонено</Table.Th>
                <Table.Th>Ждут</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {keywords.map((row) => (
                <Table.Tr key={row.keyword}>
                  <Table.Td>
                    <Text size="sm">{row.keyword}</Text>
                  </Table.Td>
                  <Table.Td>{row.found}</Table.Td>
                  <Table.Td>{row.queued}</Table.Td>
                  <Table.Td>
                    {row.accepted > 0 ? (
                      <Badge variant="light" color="green">
                        {row.accepted}
                      </Badge>
                    ) : (
                      0
                    )}
                  </Table.Td>
                  <Table.Td>{row.rejected}</Table.Td>
                  <Table.Td>{row.pending}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      ) : null}
    </Stack>
  );
}
