/**
 * Диалоги: список переписок с донорами.
 *
 * **Строка — это донор, а не адрес.** У сайта несколько адресов —
 * `info@`, `editor@`, `advertising@`, — и за ними одна редакция. Иначе
 * один донор занимает пять строк, и непонятно, с кем из них уже
 * договорились.
 *
 * **Состояние считает сервер по последнему событию**, а не хранит полем:
 * отдельное поле рассинхронизируется с письмами при первом же сбое,
 * и список врёт именно там, где по нему принимают решения.
 */

import {
  Alert,
  Badge,
  Card,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { THREAD_STATES } from '../api/labels';
import { listThreads } from '../api/outreach';
import type { ThreadCard, ThreadState } from '../api/types';

const THREADS_QUERY_KEY = ['threads'] as const;

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

function when(moment: string | null): string {
  return moment === null ? '—' : new Date(moment).toLocaleString('ru-RU');
}

function price(thread: ThreadCard): string {
  if (thread.price_white === null && thread.price_grey === null) return '—';
  const parts = [thread.price_white, thread.price_grey].filter((value) => value !== null);
  return `${parts.join(' / ')} ${thread.currency ?? ''}`.trim();
}

export function ThreadsPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [state, setState] = useState<ThreadState | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: THREADS_QUERY_KEY,
    queryFn: listThreads,
  });

  // `data ?? []` в теле создаёт новый массив на каждую отрисовку, и
  // счётчики пересчитывались бы всегда. Пустой список — константа.
  const threads = useMemo(() => data ?? [], [data]);
  const counts = useMemo(() => {
    const totals = new Map<ThreadState, number>();
    for (const thread of threads) {
      totals.set(thread.state, (totals.get(thread.state) ?? 0) + 1);
    }
    return totals;
  }, [threads]);

  const shown = threads.filter((thread) => {
    const matchesState = state === null || thread.state === state;
    const needle = search.trim().toLowerCase();
    const matchesSearch =
      needle === '' ||
      thread.host.toLowerCase().includes(needle) ||
      (thread.contact_email ?? '').toLowerCase().includes(needle);
    return matchesState && matchesSearch;
  });

  if (isLoading) return <Loader aria-label="Загружаем диалоги" m="md" />;
  if (error) {
    return (
      <Alert color="red" title="Диалоги не загрузились" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <Group justify="space-between" align="flex-start">
            <Stack gap={6}>
              <Title order={3}>Диалоги</Title>
              <Text size="sm" c="dimmed" maw={620}>
                Состояние диалога считается по последнему событию: автоответчик ответом не
                считается, а отказ доставки останавливает цепочку и метит контакт.
              </Text>
            </Stack>
            <TextInput
              placeholder="Донор или адрес"
              value={search}
              w={260}
              aria-label="Поиск по донору или адресу"
              onChange={(event) => setSearch(event.currentTarget.value)}
            />
          </Group>

          {/* Счётчики — это и фильтр: щелчок по состоянию оставляет только его.
              Отдельный список фильтров рядом со счётчиками означал бы два
              места, говорящих об одном. */}
          <Group gap="xs">
            <Badge
              variant={state === null ? 'filled' : 'light'}
              color="lagoon"
              className="press"
              style={{ cursor: 'pointer' }}
              onClick={() => setState(null)}
            >
              все — {threads.length}
            </Badge>
            {[...counts.entries()].map(([value, count]) => (
              <Badge
                key={value}
                variant={state === value ? 'filled' : 'light'}
                color={THREAD_STATES[value].color}
                className="press"
                style={{ cursor: 'pointer' }}
                onClick={() => setState(state === value ? null : value)}
              >
                {THREAD_STATES[value].title} — {count}
              </Badge>
            ))}
          </Group>
        </Stack>
      </Card>

      <Card className="glass" p="xs">
        {shown.length === 0 ? (
          <Text size="sm" c="dimmed" p="lg">
            Под фильтр ничего не попало. Это не пустая база: всего диалогов {threads.length}.
          </Text>
        ) : (
          <Table className="dataTable" verticalSpacing="sm" horizontalSpacing="md" miw={860}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Донор</Table.Th>
                <Table.Th>Состояние</Table.Th>
                <Table.Th>Писем ушло</Table.Th>
                <Table.Th>Цена</Table.Th>
                <Table.Th>Последнее событие</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {shown.map((thread) => (
                <Table.Tr
                  key={thread.id}
                  style={{ cursor: 'pointer' }}
                  onClick={() => void navigate(`/threads/${thread.id}`)}
                >
                  <Table.Td>
                    <Text fw={500}>{thread.host}</Text>
                    <Text size="xs" c="dimmed">
                      {thread.contact_email ?? 'адрес не определён'}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Group justify="center">
                      <Badge variant="light" color={THREAD_STATES[thread.state].color}>
                        {THREAD_STATES[thread.state].title}
                      </Badge>
                    </Group>
                  </Table.Td>
                  <Table.Td>{thread.messages_sent}</Table.Td>
                  <Table.Td>{price(thread)}</Table.Td>
                  <Table.Td>
                    <Text size="sm" c="dimmed">
                      {when(thread.last_event_at)}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>
    </Stack>
  );
}
