/**
 * Вкладка «Стоп-лист»: кому продажи не пишут — клиенты и партнёры — и кого держит общий стоп-лист.
 *
 * **Свой список правят здесь** (до этой вкладки — только консолью `outreach sales-stoplist-add`):
 * вставка списком или файлом — окном `StoplistAddModal`, снятие записи — с подтверждением.
 * Пополнение снимает лидов, которых закрыли новые записи; снятие возвращает в «новые» тех, кого
 * закрывала только она, — до писем их проверит очистка. Обе вещи делает сервер правилом очистки,
 * экран называет числа.
 *
 * **Общий стоп-лист здесь только виден**: отписавшиеся и пожаловавшиеся с любого этапа и записи
 * без этапа тоже держат продажи, но снимают их на экране «Стоп-лист» — там решение адресата
 * снимают с причиной в журнале. Виден он с правом смотреть базу, как общий экран: в нём отписки
 * доноров и рекламодателей с адресами, и без права сервер его не присылает.
 *
 * Права — как у прочих правок модуля: смотреть — «Продажи», менять — ещё и отправка писем.
 */

import { Alert, Button, Group, Loader, Stack, Table, Text } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { readSalesStoplist } from '../api/salesStoplist';
import type { SalesStopRow } from '../api/salesStoplist';
import { useSession } from '../auth/AuthProvider';
import { InfoHint } from '../components/InfoHint';
import { Seams } from '../components/Seams';
import { formatDate, formatNumber } from '../format';
import { notify } from '../notices';
import { FixedTable } from './FixedTable';
import type { Column } from './FixedTable';
import { addedLine, StoplistAddModal } from './StoplistAddModal';
import { StoplistGeneral } from './StoplistGeneral';
import { ConfirmRemove, useRemove } from './StoplistRemove';
import { useMayWrite, WriteRight } from './WriteRight';

const STOPLIST_QUERY_KEY = ['sales', 'stoplist'] as const;

/** Колонки своего списка. Ширины — по самому длинному: дата — 75 px, «Снять» — 60 px,
 *  автор — адрес учётки, переносится по швам; плюс поля ячейки. */
const OWN_COLUMNS: Column[] = [
  { title: 'Кому не пишем' },
  { title: 'Причина', width: '14rem' },
  { title: 'Кто внёс', width: '13rem' },
  { title: 'Когда', width: '7.5rem' },
  { title: 'Действия', width: '7rem' },
];
const OWN_MIN_WIDTH = 880;

function OwnRow({
  row,
  mayWrite,
  onRemove,
}: {
  row: SalesStopRow;
  mayWrite: boolean;
  onRemove: (row: SalesStopRow) => void;
}) {
  const target = row.host ?? row.email ?? '—';
  return (
    <Table.Tr>
      <Table.Td className="cellName">
        <Seams text={target} address />
      </Table.Td>
      <Table.Td className="wrapCell">
        <Text size="sm">{row.note ?? '—'}</Text>
      </Table.Td>
      <Table.Td className="wrapCell cellName">
        <Seams text={row.created_by ?? '—'} address />
      </Table.Td>
      <Table.Td>{formatDate(row.created_at)}</Table.Td>
      <Table.Td>
        <Button
          variant="subtle"
          size="compact-sm"
          className="press"
          disabled={!mayWrite}
          aria-label={`Снять ${target} со стоп-листа продаж`}
          onClick={() => onRemove(row)}
        >
          Снять
        </Button>
      </Table.Td>
    </Table.Tr>
  );
}

export function StoplistPane() {
  const client = useQueryClient();
  const mayWrite = useMayWrite();
  const { can } = useSession();
  const stoplist = useQuery({ queryKey: STOPLIST_QUERY_KEY, queryFn: readSalesStoplist });
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<SalesStopRow | null>(null);
  const remove = useRemove(() => setRemoving(null));
  const reread = () => client.invalidateQueries({ queryKey: ['sales'] });

  if (stoplist.data === undefined) {
    return stoplist.error ? (
      <Alert color="red" title="Стоп-лист продаж не загрузился">
        {refusalOf(stoplist.error)}
      </Alert>
    ) : (
      <Loader aria-label="Загружаем стоп-лист продаж" m="md" />
    );
  }
  const view = stoplist.data;
  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="md" px="md">
        <Stack gap={4} style={{ flex: '1 1 20rem', minWidth: 0 }}>
          <Group gap={4} wrap="nowrap" align="flex-start">
            <Text size="sm">Кому продажи не пишут: клиенты и партнёры.</Text>
            <InfoHint name="Как работает стоп-лист продаж" width={360}>
              Домен закрывает адреса на нём и компанию с ним, адрес — одного человека. Лиды под
              новой записью снимаются сразу, той же проверкой, что у очистки; снятая запись
              возвращает их в «новые».
            </InfoHint>
          </Group>
          <Text size="sm" c="dimmed">
            Записей — {formatNumber(view.total)}
          </Text>
          {!mayWrite && <WriteRight what="Правит стоп-лист" />}
        </Stack>
        <Button className="press" disabled={!mayWrite} onClick={() => setAdding(true)}>
          Добавить…
        </Button>
      </Group>
      {view.total === 0 ? (
        <Text size="sm" c="dimmed" px="md">
          Стоп-лист продаж пуст. Список клиентов и партнёров вставляют кнопкой «Добавить…» — разом.
        </Text>
      ) : (
        <FixedTable
          columns={OWN_COLUMNS}
          minWidth={OWN_MIN_WIDTH}
          className="stoplistTable"
          label="Стоп-лист продаж"
          horizontalSpacing="md"
          rows={view.rows.map((row) => (
            <OwnRow key={row.id} row={row} mayWrite={mayWrite} onRemove={setRemoving} />
          ))}
        />
      )}
      {can('view') && <StoplistGeneral view={view} />}
      {adding && (
        <StoplistAddModal
          noteMax={view.note_max}
          onClose={() => setAdding(false)}
          onPartly={() => void reread()}
          onDone={(result) => {
            setAdding(false);
            notify({ message: addedLine(result), color: 'green' });
            void reread();
          }}
        />
      )}
      {removing !== null && (
        <ConfirmRemove
          row={removing}
          busy={remove.isPending}
          error={remove.error}
          onKeep={() => setRemoving(null)}
          onRemove={() => remove.mutate(removing)}
        />
      )}
    </Stack>
  );
}
