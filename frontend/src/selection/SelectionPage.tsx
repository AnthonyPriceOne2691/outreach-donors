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
 *
 * **Смена вкладки или фильтра — не перезагрузка** (25.09.2026). Раньше
 * новый ключ запроса заменял весь экран крутилкой, а поиск терял поле на
 * каждой букве: поле пропадало вместе с экраном. Теперь сводка и фильтры
 * стоят на месте, прежние строки видны приглушёнными до прихода новых,
 * а поиск уходит на сервер после паузы в наборе.
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
import { useDebouncedValue, useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { HUMAN_INTENTS, JUDGE_DECIDERS, SELECTION_TABS } from '../api/labels';
import { decideSite, listSelection } from '../api/selection';
import type { HumanIntent, JudgeDecider, SelectionCard, SelectionTab } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { TYPING_PAUSE_MS } from '../donors/useTyped';
import { SelectionRow } from './SelectionRow';

const PAGE_SIZE = 50;

/** Колонки и их ширины — по самому длинному содержимому, домену остаток:
 *  значок «подходит» ужимался до «подхо…» на вкладке «К разбору» (аудит
 *  25.09.2026), потому что колонку порогов сжимала соседняя. Решение
 *  человека — три кнопки в ряд (~312 px); у судьи редкий длинный ряд
 *  значков переносится целыми значками. */
const WIDTH = {
  thresholds: '11rem',
  judge: '20rem',
  seller: '9.5rem',
  human: '21.5rem',
} as const;

/** Уже этого таблица не сжимается и уезжает в прокрутку: колонкам — их
 *  ширины, домену — не меньше двухсот. */
const MIN_WIDTH = 1180;
const QUERY_KEY = ['selection'] as const;
const TABS = Object.keys(SELECTION_TABS) as SelectionTab[];
const DECIDERS = Object.keys(JUDGE_DECIDERS) as JudgeDecider[];

/** Что лежит на пустой вкладке — словами, а не молчанием. */
const EMPTY: Record<SelectionTab, string> = {
  accepted: 'Принятых под фильтр нет.',
  review: 'Разбирать нечего: судья ни о ком не попросил посмотреть, у всех доноров есть данные.',
  rejected: 'Отклонённых под фильтр нет.',
};

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
  const [onlyAnswered, setOnlyAnswered] = useState(false);
  const [page, setPage] = useState(1);
  // Поиск уходит на сервер после паузы в наборе: иначе каждая буква —
  // запрос, и «3» на пути к «30» — отдельный фильтр.
  const [typed] = useDebouncedValue(search.trim(), TYPING_PAUSE_MS);
  // На узком окне три вкладки в ряд резали «Отклонены — 727» до «О».
  const narrow = useMediaQuery('(max-width: 36em)');

  const query = useQuery({
    queryKey: [
      ...QUERY_KEY,
      tab,
      typed,
      decider,
      onlyDisagreements,
      onlyUnreviewed,
      onlyUnjudged,
      onlyAnswered,
      page,
    ],
    queryFn: () =>
      listSelection({
        tab,
        ...(typed !== '' ? { search: typed } : {}),
        ...(decider !== null ? { decided_by: decider } : {}),
        only_disagreements: onlyDisagreements,
        only_unreviewed: onlyUnreviewed,
        only_unjudged: onlyUnjudged,
        only_answered: onlyAnswered,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
    // Смена вкладки или фильтра не убирает экран: прежние строки стоят,
    // пока едут новые.
    placeholderData: keepPreviousData,
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

  if (query.data === undefined) {
    // Первого ответа ещё нет — или не будет: крутилка только до первого
    // ответа, дальше экран не пропадает.
    return query.error ? (
      <Alert color="red" title="Отбор не загрузился" m="md">
        {refusalOf(query.error)}
      </Alert>
    ) : (
      <Loader aria-label="Загружаем отбор" m="md" />
    );
  }

  const data = query.data;
  // Строки прежней вкладки или фильтра — ждут замены: решать по ним нельзя.
  const stale = query.isPlaceholderData;
  const { rows, tabs, reviewed, disagreements } = data;
  const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
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

          <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }} spacing="sm">
            {TABS.map((value) => (
              <Metric key={value} title={SELECTION_TABS[value].title} value={tabs[value]} />
            ))}
            <Metric title="Ответили доноры" value={data.answered} />
            <Metric title="Смотрел человек" value={reviewed} />
            <Metric
              title="Расходится с судьёй"
              value={disagreements}
              hint={share(disagreements, reviewed)}
              color={disagreements > 0 ? 'yellow' : undefined}
            />
          </SimpleGrid>

          {/* Главное число для гест-постинга: угадал ли судья, продаёт ли сайт
              размещение, — по ответам самих сайтов. Сходимость с человеком
              отвечает на другой вопрос: «издание или продавец своего». */}
          <Text size="sm">
            Судья угадал по ответам доноров:{' '}
            {DECIDERS.map((who) => {
              const score = data.answer_layers[who];
              return `${JUDGE_DECIDERS[who].title} ${
                score === undefined ? '— ответов нет' : `${score.agreed} из ${score.checked}`
              }`;
            }).join(' · ')}
          </Text>
          <Text size="sm" c="dimmed">
            Сходится с человеком:{' '}
            {DECIDERS.map((who) => {
              const score = data.layers[who];
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
            orientation={narrow ? 'vertical' : 'horizontal'}
            fullWidth={narrow}
            value={tab}
            onChange={(value) => reset(setTab)(value as SelectionTab)}
            data={TABS.map((value) => ({
              value,
              label: `${SELECTION_TABS[value].title} — ${tabs[value]}`,
            }))}
          />
          {/* Два ряда: поля и флажки. Одним рядом на 1440 четвёртый флажок
              переносился один (аудит 25.09.2026). */}
          <Group gap="md" align="flex-end">
            <TextInput
              placeholder="Домен или причина"
              aria-label="Поиск по домену или причине"
              w={{ base: '100%', xs: 260 }}
              value={search}
              onChange={(event) => reset(setSearch)(event.currentTarget.value)}
            />
            <Select
              aria-label="Кто решил у судьи"
              placeholder="Кто решил у судьи"
              w={{ base: '100%', xs: 200 }}
              clearable
              value={decider}
              onChange={(value) => reset(setDecider)(value as JudgeDecider | null)}
              data={DECIDERS.map((who) => ({ value: who, label: JUDGE_DECIDERS[who].title }))}
              comboboxProps={{ width: 'target', position: 'bottom-start' }}
            />
          </Group>
          <Group gap="lg">
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
              label="Донор ответил"
              checked={onlyAnswered}
              onChange={(event) => reset(setOnlyAnswered)(event.currentTarget.checked)}
            />
            <Switch
              label="Судья не смотрел"
              checked={onlyUnjudged}
              onChange={(event) => reset(setOnlyUnjudged)(event.currentTarget.checked)}
            />
          </Group>
        </Stack>
      </Card>

      <Card
        className="glass staleRows"
        p="xs"
        data-stale={stale || undefined}
        aria-busy={stale || undefined}
      >
        {rows.length === 0 ? (
          <Text size="sm" c="dimmed" p="lg">
            {stale ? 'Загружаем…' : EMPTY[tab]}
          </Text>
        ) : (
          <Table.ScrollContainer minWidth={MIN_WIDTH} type="native" className="scrollSlim">
            <Table
              className="dataTable selectionTable"
              layout="fixed"
              verticalSpacing="sm"
              horizontalSpacing="md"
            >
              <colgroup>
                <col />
                <col style={{ width: WIDTH.thresholds }} />
                <col style={{ width: WIDTH.judge }} />
                <col style={{ width: WIDTH.seller }} />
                <col style={{ width: WIDTH.human }} />
              </colgroup>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Домен</Table.Th>
                  <Table.Th>Пороги</Table.Th>
                  <Table.Th>Судья</Table.Th>
                  <Table.Th>Донор ответил</Table.Th>
                  <Table.Th>Человек</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((row) => (
                  <SelectionRow
                    key={row.domain_id}
                    row={row}
                    mayDecide={mayDecide}
                    busy={
                      stale ||
                      (decide.isPending && decide.variables?.row.domain_id === row.domain_id)
                    }
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
