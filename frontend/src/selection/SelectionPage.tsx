/**
 * Отбор: кто принят, кто отклонён, кем и почему.
 *
 * **Экран существует ради двух чисел** — сколько мусора прошло отбор и
 * сколько годного он отсеял. Оба считаются только по расхождению
 * человека с машиной, поэтому решение человека стоит в той же строке,
 * что и вердикт, а сходимость — сверху, по каждому слою судьи отдельно:
 * правилу верят без взгляда человека, и его ошибка — другая новость,
 * чем ошибка модели.
 *
 * **Отклонённые видны.** Отклонённый без следа — слепая зона, в которой
 * ложно отсеянных не видит никто; поэтому вкладка отказов не хуже
 * вкладки приёма, а у каждого отказа названы автор и основание.
 *
 * **Вердикт машины решением человека не переписывается** — сервер держит
 * оба, экран показывает оба.
 */

import {
  Alert,
  Card,
  Group,
  Loader,
  Pagination,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { HUMAN_INTENTS, JUDGE_DECIDERS, SELECTION_TABS } from '../api/labels';
import { decideSite, listSelection } from '../api/selection';
import type { HumanIntent, JudgeDecider, SelectionCard, SelectionTab } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { SelectionRow } from './SelectionRow';

const PAGE_SIZE = 50;
const QUERY_KEY = ['selection'] as const;
const TABS = Object.keys(SELECTION_TABS) as SelectionTab[];
const DECIDERS = Object.keys(JUDGE_DECIDERS) as JudgeDecider[];

/** Что лежит на пустой вкладке — словами, а не молчанием. */
const EMPTY: Record<SelectionTab, string> = {
  accepted: 'Принятых под фильтр нет.',
  review: 'Разбирать нечего: судья ни о ком не попросил посмотреть, у всех доноров есть данные.',
  rejected: 'Отклонённых под фильтр нет.',
};

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

function share(part: number, whole: number): string {
  return whole === 0 ? '—' : `${Math.round((part / whole) * 100)}%`;
}

export function SelectionPage() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<SelectionTab>('accepted');
  const [search, setSearch] = useState('');
  const [decider, setDecider] = useState<JudgeDecider | null>(null);
  const [onlyDisagreements, setOnlyDisagreements] = useState(false);
  const [onlyUnreviewed, setOnlyUnreviewed] = useState(false);
  const [onlyUnjudged, setOnlyUnjudged] = useState(false);
  const [page, setPage] = useState(1);

  const query = useQuery({
    queryKey: [
      ...QUERY_KEY,
      tab,
      search,
      decider,
      onlyDisagreements,
      onlyUnreviewed,
      onlyUnjudged,
      page,
    ],
    queryFn: () =>
      listSelection({
        tab,
        ...(search.trim() !== '' ? { search: search.trim() } : {}),
        ...(decider !== null ? { decided_by: decider } : {}),
        only_disagreements: onlyDisagreements,
        only_unreviewed: onlyUnreviewed,
        only_unjudged: onlyUnjudged,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
  });

  const decide = useMutation({
    mutationFn: ({ row, intent }: { row: SelectionCard; intent: HumanIntent | null }) =>
      decideSite(row.domain_id, intent),
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      const said = row.human.intent === null ? 'решение снято' : HUMAN_INTENTS[row.human.intent];
      notifications.show({ message: `${row.host}: ${said}`, color: 'green' });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не записали', message: refusalOf(failure), color: 'red' }),
  });

  // Любой фильтр возвращает на первую страницу: иначе сужение выдачи
  // оставляет человека на странице, которой больше нет.
  const reset =
    <T,>(set: (value: T) => void) =>
    (value: T) => {
      set(value);
      setPage(1);
    };

  if (query.isLoading && query.data === undefined) {
    return <Loader aria-label="Загружаем отбор" m="md" />;
  }
  if (query.error) {
    return (
      <Alert color="red" title="Отбор не загрузился" m="md">
        {refusalOf(query.error)}
      </Alert>
    );
  }

  const data = query.data;
  const rows = data?.rows ?? [];
  const tabs = data?.tabs ?? { accepted: 0, review: 0, rejected: 0 };
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE));
  const reviewed = data?.reviewed ?? 0;
  const disagreements = data?.disagreements ?? 0;
  const mayDecide = can('prices');

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <Stack gap={6}>
            <Title order={3}>Отбор</Title>
            <Text size="sm" c="dimmed" maw={720}>
              Каждый домен лежит ровно на одной вкладке. Отклонённый — не прошёл пороги или судья
              решил, что это не площадка; у каждого отказа названы автор и основание. Решение
              человека сильнее судьи, но вердикт судьи не переписывает: по расхождению между ними
              видно, как часто он ошибается.
            </Text>
          </Stack>

          <SimpleGrid cols={{ base: 2, sm: 5 }} spacing="sm">
            {TABS.map((value) => (
              <Metric key={value} title={SELECTION_TABS[value].title} value={tabs[value]} />
            ))}
            <Metric title="Смотрел человек" value={reviewed} />
            <Metric
              title="Расходится с судьёй"
              value={disagreements}
              hint={share(disagreements, reviewed)}
              color={disagreements > 0 ? 'yellow' : undefined}
            />
          </SimpleGrid>

          <Text size="sm" c="dimmed">
            Сходится с человеком:{' '}
            {DECIDERS.map((who) => {
              const score = data?.layers[who];
              return `${JUDGE_DECIDERS[who].title} ${
                score === undefined ? '— не проверяли' : `${score.agreed} из ${score.checked}`
              }`;
            }).join(' · ')}
          </Text>
        </Stack>
      </Card>

      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <SegmentedControl
            value={tab}
            onChange={(value) => reset(setTab)(value as SelectionTab)}
            data={TABS.map((value) => ({
              value,
              label: `${SELECTION_TABS[value].title} — ${tabs[value]}`,
            }))}
          />
          <Group gap="md" align="flex-end">
            <TextInput
              placeholder="Домен или причина"
              aria-label="Поиск по домену или причине"
              w={260}
              value={search}
              onChange={(event) => reset(setSearch)(event.currentTarget.value)}
            />
            <Select
              aria-label="Кто решил у судьи"
              placeholder="Кто решил у судьи"
              w={200}
              clearable
              value={decider}
              onChange={(value) => reset(setDecider)(value as JudgeDecider | null)}
              data={DECIDERS.map((who) => ({ value: who, label: JUDGE_DECIDERS[who].title }))}
              comboboxProps={{ width: 'target', position: 'bottom-start' }}
            />
            <Switch
              label="Только расхождения"
              checked={onlyDisagreements}
              onChange={(event) => reset(setOnlyDisagreements)(event.currentTarget.checked)}
            />
            <Switch
              label="Человек не смотрел"
              checked={onlyUnreviewed}
              onChange={(event) => reset(setOnlyUnreviewed)(event.currentTarget.checked)}
            />
            <Switch
              label="Судья не смотрел"
              checked={onlyUnjudged}
              onChange={(event) => reset(setOnlyUnjudged)(event.currentTarget.checked)}
            />
          </Group>
        </Stack>
      </Card>

      <Card className="glass" p="xs">
        {rows.length === 0 ? (
          <Text size="sm" c="dimmed" p="lg">
            {EMPTY[tab]}
          </Text>
        ) : (
          <Table.ScrollContainer minWidth={960}>
            <Table className="dataTable" verticalSpacing="sm" horizontalSpacing="md">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Домен</Table.Th>
                  <Table.Th>Пороги</Table.Th>
                  <Table.Th>Судья</Table.Th>
                  <Table.Th>Человек</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((row) => (
                  <SelectionRow
                    key={row.domain_id}
                    row={row}
                    mayDecide={mayDecide}
                    busy={decide.isPending && decide.variables?.row.domain_id === row.domain_id}
                    onDecide={(target, intent) => decide.mutate({ row: target, intent })}
                  />
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>

      {pages > 1 && (
        <Group justify="center">
          <Pagination value={page} onChange={setPage} total={pages} radius="xl" />
        </Group>
      )}
    </Stack>
  );
}
