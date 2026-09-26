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
 *
 * **Сводка и одна панель под ней** (замечание 26.09.2026). Плитки — одного
 * вида и на одном уровне: пояснение с долей у последней держало пустую
 * строку под числом у всех шести. Фильтры переехали из отдельной карточки
 * в строку под заголовками колонок (`SelectionTable`), и над таблицей
 * остался один переключатель вкладок — он стал шапкой панели с таблицей,
 * а не карточкой ради одной строки: шапка, таблица с фильтрами и страницы —
 * одна панель, как у доноров. По двадцать на страницу; вкладка, фильтры
 * и страница — в адресе (`selectionFilters.ts`).
 */

import {
  Alert,
  Box,
  Card,
  Group,
  Loader,
  Pagination,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { HUMAN_INTENTS, JUDGE_DECIDERS, SELECTION_TABS } from '../api/labels';
import { decideSite, listSelection } from '../api/selection';
import type { HumanIntent, JudgeDecider, SelectionCard, SelectionView } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { useTyped } from '../donors/useTyped';
import { formatNumber } from '../format';
import {
  emptinessOf,
  NO_SELECTION_FILTERS,
  queryOf,
  readSelectionFilters,
  SELECTION_TAB_KEYS,
  writeSelectionFilters,
} from './selectionFilters';
import type { SelectionFilters } from './selectionFilters';
import { SelectionTable } from './SelectionTable';

const QUERY_KEY = ['selection'] as const;
const DECIDERS = Object.keys(JUDGE_DECIDERS) as JudgeDecider[];

/** Набранный поиск совпадает с адресом без пробелов по краям: пробел в конце —
 *  это ещё набор, а не новый фильтр. */
const sameSearch = (draft: string, committed: string) => draft.trim() === committed;

/** Стрелки переключателя страниц словами — те же, что у доноров и истории. */
const CONTROL_NAMES: Record<string, string> = {
  previous: 'Предыдущая страница',
  next: 'Следующая страница',
  first: 'Первая страница',
  last: 'Последняя страница',
};

type Score = Partial<Record<JudgeDecider, { checked: number; agreed: number }>>;

/** «правило 2 из 2 · модель 1 из 2 · арбитр — не проверяли». */
function scoreLine(score: Score, missing: string): string {
  return DECIDERS.map((who) => {
    const layer = score[who];
    return `${JUDGE_DECIDERS[who].title} ${
      layer === undefined
        ? `— ${missing}`
        : `${formatNumber(layer.agreed)} из ${formatNumber(layer.checked)}`
    }`;
  }).join(' · ');
}

/** Сводка по всему отбору, а не по странице: она отвечает на вопрос «насколько
 *  верить машине», и фильтры таблицы её не трогают. */
function Summary({ data }: { data: SelectionView }) {
  return (
    <Card className="glassPanel" p="xl">
      <Stack gap="md">
        <Stack gap={6}>
          <Title order={3}>Отбор</Title>
          <Text size="sm" c="dimmed" maw={720}>
            Каждый домен лежит ровно на одной вкладке. Отклонённый — не прошёл пороги или судья
            решил, что это не площадка; у каждого отказа названы автор и основание. Решение человека
            сильнее судьи, но вердикт судьи не переписывает: по расхождению между ними видно, как
            часто он ошибается.
          </Text>
        </Stack>

        {/* Плитки без пояснений: доля расхождений («—» до первого решения
            человека) держала пустую строку под числом у всех шести плиток
            и опускала их содержимое ниже середины (замечание 26.09.2026). */}
        <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }} spacing="sm">
          {SELECTION_TAB_KEYS.map((value) => (
            <Metric
              key={value}
              title={SELECTION_TABS[value].title}
              value={formatNumber(data.tabs[value])}
            />
          ))}
          <Metric title="Ответили доноры" value={formatNumber(data.answered)} />
          <Metric title="Смотрел человек" value={formatNumber(data.reviewed)} />
          <Metric
            title="Расходится с судьёй"
            value={formatNumber(data.disagreements)}
            color={data.disagreements > 0 ? 'yellow' : undefined}
          />
        </SimpleGrid>

        {/* Главное число для гест-постинга: угадал ли судья, продаёт ли сайт
            размещение, — по ответам самих сайтов. Сходимость с человеком
            отвечает на другой вопрос: «издание или продавец своего». */}
        <Text size="sm">
          Судья угадал по ответам доноров: {scoreLine(data.answer_layers, 'ответов нет')}
        </Text>
        <Text size="sm" c="dimmed">
          Сходится с человеком: {scoreLine(data.layers, 'не проверяли')}
        </Text>
      </Stack>
    </Card>
  );
}

export function SelectionPage() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => readSelectionFilters(params), [params]);
  // На узком окне три вкладки в ряд резали «Отклонены — 727» до «О».
  const narrow = useMediaQuery('(max-width: 36em)');
  // На телефоне переключатель страниц без соседей текущей: с ними кнопки
  // не влезали в строку (урок доноров).
  const phone = useMediaQuery('(max-width: 30em)') === true;

  // Смена вкладки или фильтра — замена записи в истории и первая страница:
  // «назад» ведёт туда, откуда пришли, а не по буквам поиска, а двадцатая
  // страница старого вопроса ничего не говорит о новом. Страница — новая
  // запись.
  const apply = useCallback(
    (patch: Partial<SelectionFilters>) =>
      setParams(
        (current) => writeSelectionFilters({ ...readSelectionFilters(current), ...patch, page: 1 }),
        { replace: true },
      ),
    [setParams],
  );
  const turn = useCallback(
    (page: number) =>
      setParams((current) => writeSelectionFilters({ ...readSelectionFilters(current), page })),
    [setParams],
  );

  // Поиск печатают: в адрес и на сервер он уходит после паузы в наборе.
  const [search, setSearch] = useTyped(
    filters.search,
    (value) => apply({ search: value.trim() }),
    sameSearch,
  );

  const query = useQuery({
    queryKey: [
      ...QUERY_KEY,
      filters.tab,
      filters.search,
      filters.thresholds,
      filters.judge,
      filters.answer,
      filters.human,
      filters.page,
    ],
    queryFn: () => listSelection(queryOf(filters)),
    // Смена вкладки, фильтра или страницы не убирает экран: прежние строки
    // стоят, пока едут новые.
    placeholderData: keepPreviousData,
  });

  // Сводка не зависит от фильтров, и отказ сервера на новом фильтре не должен
  // убирать её с экрана вместе с фильтрами: отказ встаёт строкой в таблицу,
  // а сверху остаётся последняя пришедшая сводка.
  const [lastView, setLastView] = useState<SelectionView | undefined>(undefined);
  useEffect(() => {
    if (query.data !== undefined) setLastView(query.data);
  }, [query.data]);
  const data = query.data ?? lastView;

  const answer = query.isPlaceholderData ? undefined : query.data;
  // Страницы — по ответу на этот вопрос (или по прежнему, пока едет новый),
  // а не по последней сводке: под строкой отказа переключатель страниц
  // прежнего вопроса звал бы листать то, чего на экране нет.
  const pages =
    query.data === undefined ? 1 : Math.max(1, Math.ceil(query.data.total / query.data.limit));

  // Страница из старой ссылки может оказаться за концом: вместо пустоты
  // с «ничего не нашлось» — последняя настоящая страница.
  useEffect(() => {
    if (answer !== undefined && answer.rows.length === 0 && answer.total > 0) {
      const last = Math.max(1, Math.ceil(answer.total / answer.limit));
      if (filters.page > last) {
        setParams(
          (current) => writeSelectionFilters({ ...readSelectionFilters(current), page: last }),
          { replace: true },
        );
      }
    }
  }, [answer, filters.page, setParams]);

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

  if (data === undefined) {
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

  // Строки прежней вкладки или фильтра — ждут замены: решать по ним нельзя.
  const stale = query.isPlaceholderData;
  const refusal = query.data === undefined && query.error ? refusalOf(query.error) : null;
  const rows = query.data?.rows ?? [];

  return (
    <Stack gap="lg">
      <Summary data={data} />

      <Card className="glassPanel" p="md">
        <Stack gap="sm">
          <SegmentedControl
            orientation={narrow ? 'vertical' : 'horizontal'}
            fullWidth={narrow}
            aria-label="Вкладки отбора"
            value={filters.tab}
            onChange={(value) => {
              const tab = SELECTION_TAB_KEYS.find((key) => key === value);
              if (tab !== undefined) apply({ tab });
            }}
            data={SELECTION_TAB_KEYS.map((value) => ({
              value,
              label: `${SELECTION_TABS[value].title} — ${formatNumber(data.tabs[value])}`,
            }))}
          />

          <SelectionTable
            rows={rows}
            stale={stale}
            refusal={refusal}
            empty={
              answer !== undefined && rows.length === 0
                ? emptinessOf(filters, answer.tabs[filters.tab])
                : null
            }
            filters={filters}
            search={search}
            onSearch={setSearch}
            onFilter={apply}
            mayDecide={can('prices')}
            deciding={decide.isPending ? (decide.variables?.row.domain_id ?? null) : null}
            onDecide={(row, intent) => decide.mutate({ row, intent })}
            onReset={() => {
              setSearch('');
              setParams(writeSelectionFilters({ ...NO_SELECTION_FILTERS, tab: filters.tab }), {
                replace: true,
              });
            }}
          />

          {pages > 1 && (
            // Тот же вид, что у доноров: навигация с именем, номер — в своём
            // элементе (по нему меряют контраст, а не по кругу кнопки).
            <Box component="nav" aria-label="Страницы отбора" pt="xs">
              <Group justify="center">
                <Pagination
                  value={Math.min(filters.page, pages)}
                  onChange={turn}
                  total={pages}
                  siblings={phone ? 0 : 1}
                  radius="xl"
                  getItemProps={(number) => ({
                    'aria-label': `Страница ${number}`,
                    children: <span data-page-number>{number}</span>,
                  })}
                  getControlProps={(control) => ({ 'aria-label': CONTROL_NAMES[control] })}
                />
              </Group>
            </Box>
          )}
        </Stack>
      </Card>
    </Stack>
  );
}
