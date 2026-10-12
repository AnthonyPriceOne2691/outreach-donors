/**
 * Доноры с протухшей ценой на панели «Обход доноров» — и «Перезапросить цену» по одному
 * и пачкой (12.10.2026).
 *
 * ТЗ, Этап 2: «свежесть цены 150 дней; старше — сначала перезапрос цены у донора». Обход
 * таких доноров не берёт, а панель до этой строки их только считала: «цена протухла — N».
 *
 * **Строка — только когда протухшие есть, список — в окне**: панель в покое остаётся
 * короткой (аудит экранов 09.10.2026). Окно спрашивает сервер, когда его открыли: можно ли
 * перезапросить каждого, куда уйдёт письмо и почему нельзя — словами сервера.
 *
 * **Итог — в том же окне, где нажали**, как у пачки писем (`SendQueue`): что ушло и почему
 * не ушло остальное. Список перечитывается — у ушедших появляется дата перезапроса.
 *
 * **Нажатие живёт выше окна** (проверка 12.10.2026, `donors/recheckAsk.ts`): окно закрыли
 * посреди пачки и открыли снова — кнопки заперты, пока пачка не кончилась, и итог её —
 * в окне. Иначе вторая пачка ушла бы поверх первой.
 */

import { Anchor, Button, Checkbox, Group, Loader, Modal, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import type { RecheckRow, RechecksView } from '../api/priceRecheck';
import { fetchStalePrices } from '../api/priceRecheck';
import type { Column } from '../components/ColumnsHead';
import { ColumnsHead } from '../components/ColumnsHead';
import { Seams } from '../components/Seams';
import type { RecheckAsking } from '../donors/recheckAsk';
import { useRecheckAsk } from '../donors/recheckAsk';
import { RecheckOutcome, Refused, ThreadLink } from '../donors/recheckParts';
import { formatDate, formatMoney, formatNumber, plural } from '../format';

export const STALE_PRICES_KEY = ['price-rechecks'] as const;

const COLUMNS: Column[] = [
  { title: 'Донор' },
  { title: 'Цена', width: '8.5rem' },
  { title: 'Названа', width: '7rem' },
  { title: 'Перезапрос', width: '7.5rem' },
  { title: 'Куда уйдёт письмо', width: '19rem' },
];
const PICK_WIDTH = '3.25rem';
const ACTION_WIDTH = '9.5rem';
const TABLE_MIN_WIDTH = 900;

/** Что делает перезапрос — над списком, сроками сервера. */
function about(view: RechecksView): string {
  return (
    `Цена старше ${formatNumber(view.ttl_days)} дней: оффер «мы дешевле» на ней — обещание, ` +
    'которого не сдержать. Перезапрос — короткое письмо донору: действует ли цена или какая ' +
    'теперь. Донор отвечал — письмо уйдёт в его переписку, с её ящика; переписки нет — новым ' +
    'первым письмом. Ответ с ценой сделает её свежей, и обход откроется сам. Одному донору — ' +
    `не чаще раза в ${formatNumber(view.every_days)} ${plural(view.every_days, 'день', 'дня', 'дней')}.`
  );
}

/** Куда уйдёт письмо — или почему не уйдёт, словами сервера. */
function Where({ row }: { row: RecheckRow }) {
  if (row.refusal !== null) {
    return (
      <Text size="sm" c="dimmed">
        {row.refusal}
      </Text>
    );
  }
  if (row.way === 'thread' && row.thread_id !== null) {
    return (
      <Text size="sm">
        в <ThreadLink threadId={row.thread_id} />
        {row.email ? ` — на ${row.email}` : ''}
      </Text>
    );
  }
  return <Text size="sm">новым письмом{row.email ? ` на ${row.email}` : ''}</Text>;
}

function StaleTable({
  rows,
  picked,
  onPick,
  maySend,
  pending,
  onAsk,
}: {
  rows: RecheckRow[];
  picked: Set<number>;
  onPick: (donorId: number, on: boolean) => void;
  maySend: boolean;
  pending: boolean;
  onAsk: (donorId: number) => void;
}) {
  return (
    <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
      <Table
        className={maySend ? 'dataTable fixedTable pickFirst' : 'dataTable fixedTable'}
        layout="fixed"
        tabularNums
        verticalSpacing="sm"
      >
        <ColumnsHead
          columns={COLUMNS}
          lead={maySend ? PICK_WIDTH : null}
          tail={maySend ? ACTION_WIDTH : null}
        />
        <Table.Tbody>
          {rows.map((row) => (
            <Table.Tr key={row.donor_id}>
              {maySend ? (
                <Table.Td>
                  <Group justify="center">
                    <Checkbox
                      aria-label={`Перезапросить цену ${row.host}`}
                      checked={picked.has(row.donor_id)}
                      disabled={row.refusal !== null}
                      onChange={(event) => onPick(row.donor_id, event.currentTarget.checked)}
                    />
                  </Group>
                </Table.Td>
              ) : null}
              <Table.Td className="wrapCell cellName">
                <Anchor component={Link} to={`/donors/${row.donor_id}`} size="sm">
                  <Seams text={row.host} />
                </Anchor>
              </Table.Td>
              <Table.Td>
                {formatMoney(row.price, row.currency)}
                {row.source === 'manual' ? (
                  <Text size="xs" c="dimmed">
                    вручную
                  </Text>
                ) : null}
              </Table.Td>
              <Table.Td>{formatDate(row.priced_at)}</Table.Td>
              <Table.Td>{formatDate(row.asked_at)}</Table.Td>
              <Table.Td className="wrapCell">
                <Where row={row} />
              </Table.Td>
              {maySend ? (
                <Table.Td>
                  <Button
                    size="compact-sm"
                    variant="light"
                    className="press"
                    disabled={row.refusal !== null || pending}
                    aria-label={`Перезапросить цену ${row.host}`}
                    onClick={() => onAsk(row.donor_id)}
                  >
                    Перезапросить
                  </Button>
                </Table.Td>
              ) : null}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function togglePicked(was: Set<number>, donorId: number, on: boolean): Set<number> {
  const next = new Set(was);
  if (on) next.add(donorId);
  else next.delete(donorId);
  return next;
}

/** Список в окне — с отметками и кнопками у того, кому можно писать наружу; нажатие —
 *  у панели, выше окна (`recheckAsk.ts`). */
function StaleList({ maySend, ask }: { maySend: boolean; ask: RecheckAsking }) {
  const { data, isLoading, error } = useQuery({
    queryKey: STALE_PRICES_KEY,
    queryFn: fetchStalePrices,
  });
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const send = (donorIds: number[]) =>
    ask.mutate(donorIds, { onSuccess: () => setPicked(new Set()) });
  if (isLoading) return <Loader size="sm" aria-label="Загружаем доноров с протухшей ценой" />;
  if (error) return <Refused title="Список не загрузился" error={error} />;
  if (data === undefined) return null;
  const tooMany = picked.size > data.batch_max;
  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed" maw={760}>
        {about(data)}
      </Text>
      <RecheckOutcome done={ask.data} error={ask.error} />
      <StaleTable
        rows={data.rows}
        picked={picked}
        onPick={(donorId, on) => setPicked((was) => togglePicked(was, donorId, on))}
        maySend={maySend}
        pending={ask.isPending}
        onAsk={(donorId) => send([donorId])}
      />
      {data.total > data.rows.length ? (
        <Text size="sm" c="dimmed">
          Показаны первые {formatNumber(data.rows.length)} из {formatNumber(data.total)}.
        </Text>
      ) : null}
      {maySend ? (
        <Stack gap={4}>
          <Group>
            <Button
              className="press"
              disabled={picked.size === 0 || tooMany}
              loading={ask.isPending}
              onClick={() => send([...picked])}
            >
              Перезапросить отмеченных · {formatNumber(picked.size)}
            </Button>
          </Group>
          {tooMany ? (
            <Text size="sm" c="dimmed">
              За раз — не больше {formatNumber(data.batch_max)}: письма уходят тут же, по одному.
            </Text>
          ) : null}
        </Stack>
      ) : null}
    </Stack>
  );
}

/** Строка панели: сколько протухших и кнопка к списку. Протухших нет — строки нет. */
export function StalePrices({
  count,
  maySend,
  onAsked,
}: {
  count: number;
  /** Право писать наружу (`send`): без него список — без отметок и кнопок. */
  maySend: boolean;
  /** Перезапрос ушёл — панели пора перечитать доноров. */
  onAsked: () => void;
}) {
  const [opened, setOpened] = useState(false);
  const ask = useRecheckAsk([STALE_PRICES_KEY], onAsked);
  if (count === 0) return null;
  return (
    <>
      <Group justify="space-between" align="center" gap="sm">
        <Text size="sm">
          Цена протухла у {formatNumber(count)} {plural(count, 'донора', 'доноров', 'доноров')} — их
          не обходим, пока цену не подтвердят.
        </Text>
        <Button
          variant="light"
          className="press"
          aria-haspopup="dialog"
          onClick={() => setOpened(true)}
        >
          {maySend ? 'Перезапросить цену' : 'Кому перезапросить цену'}
        </Button>
      </Group>
      <Modal opened={opened} onClose={() => setOpened(false)} title="Перезапрос цены" size="xl">
        {opened ? <StaleList maySend={maySend} ask={ask} /> : null}
      </Modal>
    </>
  );
}
