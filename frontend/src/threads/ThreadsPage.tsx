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
 *
 * **Фильтры — под своими колонками и в адресе** (`threadFilters.ts`), как
 * у доноров. Счётчики состояний живут в самом фильтре — «ждёт разбора · 1»:
 * ряд значков-счётчиков над таблицей был вторым местом, говорящим о том же,
 * а его значки звали нажать (рука, вдавливание) и не отвечали на наведение
 * (аудит 25.09.2026).
 *
 * **Ширины колонок — по самому длинному, что в них бывает**, раскладка
 * фиксированная, узкому окну — прокрутка: на телефоне таблица в карточке
 * без прокрутки показывала две колонки из пяти, а значок состояния
 * ужимался до «цепочка остановле…».
 *
 * **Вторая вкладка — «Не привязаны»** (28.09.2026): ответы, которые приём
 * сохранил, но не соотнёс ни с одним нашим письмом (`UnboundReplies`).
 * Вкладки — шапкой панели с таблицей, как на «Отборе», со счётчиками; вкладка
 * и её страница — в адресе (`threadTabs.ts`). Число непривязанных спрашивается
 * и на первой вкладке: без него вкладку, в которой что-то лежит, не отличить
 * от пустой, а первая её страница из кэша открывает вкладку сразу.
 */

import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';
import type { ReactNode } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { useSession } from '../auth/AuthProvider';
import { exportCounts, saveFile } from '../api/donors';
import { exportLeads } from '../api/outreach';
import { threadState } from '../api/labels';
import { listThreads } from '../api/outreach';
import type { ThreadCard, ThreadState } from '../api/types';
import { usePageParam } from '../components/PageSwitch';
import { Seams } from '../components/Seams';
import { useTyped } from '../donors/useTyped';
import { formatDateTime, formatMoney, formatNumber } from '../format';
import { ParseCalibration } from './ParseCalibration';
import {
  isThreadFiltered,
  NO_THREAD_FILTERS,
  readThreadFilters,
  threadEmptiness,
  threadMatches,
  THREAD_STATE_KEYS,
  writeThreadFilters,
} from './threadFilters';
import type { ThreadFilters } from './threadFilters';
import {
  readThreadTab,
  THREAD_TAB_KEYS,
  THREAD_TABS,
  useUnboundPage,
  writeThreadTab,
} from './threadTabs';
import type { ThreadTab } from './threadTabs';
import { UnboundReplies } from './UnboundReplies';

const THREADS_QUERY_KEY = ['threads'] as const;

/** Колонки слева направо. Ширина первой — остаток: в ней донор и адрес.
 *  Остальные — по самому длинному, что в них бывает, замеренному шрифтом
 *  экрана 25.09.2026 (холст по живому элементу): фильтр «не продаёт
 *  размещения · 99» с полями и шевроном — 210 px, значок этого состояния —
 *  169; «белая 12 500,00 USDT» — 152; заголовок «Последнее событие» — 142,
 *  «Писем ушло» — 88. Плюс 32 px полей ячейки и запас в несколько пикселей. */
const COLUMNS: { title: string; width?: string }[] = [
  { title: 'Донор' },
  { title: 'Состояние', width: '15.25rem' },
  { title: 'Писем ушло', width: '7.75rem' },
  { title: 'Цена', width: '11.75rem' },
  { title: 'Последнее событие', width: '11rem' },
];

/** Уже этого таблица не сжимается и уезжает в прокрутку: колонкам — их
 *  ширины (732 px), донору — не меньше двухсот. */
const TABLE_MIN_WIDTH = 940;

const sameSearch = (draft: string, committed: string) => draft.trim() === committed;

/** «Ждёт разбора · 1»: точка, как у фильтров доноров. */
function counted(title: string, count: number): string {
  return `${title} · ${formatNumber(count)}`;
}

/** «Не привязаны — 2», как у вкладок «Отбора»; пока числа нет — одно имя. */
function tabLabel(tab: ThreadTab, count: number | undefined): string {
  return count === undefined ? THREAD_TABS[tab] : `${THREAD_TABS[tab]} — ${formatNumber(count)}`;
}

/** Цена — по строке на белую и серую: «250,00 € / 180,00 €» без подписей
 *  не говорило, какая из двух какая, а одна серая читалась белой. */
function PriceCell({ thread }: { thread: ThreadCard }) {
  if (thread.price_white === null && thread.price_grey === null) return <>—</>;
  return (
    <Stack gap={0} align="center">
      {thread.price_white !== null && (
        <Text size="sm">белая {formatMoney(thread.price_white, thread.currency)}</Text>
      )}
      {thread.price_grey !== null && (
        <Text size="sm" c="dimmed">
          серая {formatMoney(thread.price_grey, thread.currency)}
        </Text>
      )}
    </Stack>
  );
}

/** Одна строка на всю ширину: пусто под фильтром. Фильтры над ней остаются —
 *  поправить условие можно тут же, не возвращаясь. */
function WholeRow({ children }: { children: ReactNode }) {
  return (
    <Table.Tr className="wholeRow">
      <Table.Td colSpan={COLUMNS.length}>{children}</Table.Td>
    </Table.Tr>
  );
}

export function ThreadsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => readThreadFilters(params), [params]);
  const tab = readThreadTab(params);
  const [page, goToPage] = usePageParam();
  // На первой вкладке — ради числа во вкладке: первая страница, та же, что
  // откроется на второй.
  const unbound = useUnboundPage(tab === 'unbound' ? page : 1);
  // На узком окне вкладки — во всю ширину, поровну.
  const narrow = useMediaQuery('(max-width: 36em)') === true;

  // Смена фильтра — замена записи в истории, а не новая: «назад» ведёт туда,
  // откуда пришли, а не по буквам поиска.
  const apply = useCallback(
    (patch: Partial<ThreadFilters>) =>
      setParams((current) => writeThreadFilters({ ...readThreadFilters(current), ...patch }), {
        replace: true,
      }),
    [setParams],
  );
  // Поиск печатают: в адрес он уходит после паузы в наборе, а список
  // сужается сразу — фильтр здесь по уже пришедшим строкам, сервер не ждут.
  const [search, setSearch] = useTyped(
    filters.search,
    (value) => apply({ search: value.trim() }),
    sameSearch,
  );

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

  const typed = { ...filters, search: search.trim() };
  const shown = threads.filter((thread) => threadMatches(thread, typed));

  // Крутилка и отказ — только у своей вкладки: вторая от списка диалогов
  // не зависит и ждать его не должна.
  if (tab === 'threads' && isLoading) return <Loader aria-label="Загружаем диалоги" m="md" />;
  if (tab === 'threads' && error) {
    return (
      <Alert color="red" title="Диалоги не загрузились" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  // В списке — состояния, которые есть, и выбранное, даже если его нет:
  // иначе фильтр из адреса («лиды», когда лидов ноль) стоял бы пустым.
  const states = THREAD_STATE_KEYS.filter(
    (state) => (counts.get(state) ?? 0) > 0 || state === filters.state,
  );
  const empty = shown.length === 0 ? threadEmptiness(typed, counts, threads.length) : null;
  const open = (thread: ThreadCard) =>
    void navigate(`/threads/${thread.id}`, { state: { from: location.search } });
  const tabCounts: Record<ThreadTab, number | undefined> = {
    threads: data?.length,
    unbound: unbound.data?.total,
  };
  // Смена вкладки — замена записи в истории, как у «Отбора»; недонабранный
  // поиск сбрасывается, чтобы не догнать новую вкладку своей записью в адрес.
  const switchTab = (next: ThreadTab) => {
    setSearch('');
    setParams(writeThreadTab(next), { replace: true });
  };

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <Stack gap={6}>
            <Group gap="sm" align="baseline" wrap="nowrap">
              <Title order={3}>Диалоги</Title>
              {/* Полными чернилами, как у доноров: число стоит в углу панели,
                  на блике стекла, и приглушённый тон там не держал норму. */}
              {data !== undefined && (
                <Text size="sm" c="var(--ink)">
                  {isThreadFiltered(typed)
                    ? `найдено ${formatNumber(shown.length)} из ${formatNumber(threads.length)}`
                    : `всего ${formatNumber(threads.length)}`}
                </Text>
              )}
            </Group>
            <Text size="sm" c="dimmed" maw={620}>
              Состояние диалога считается по последнему событию: автоответчик ответом не считается,
              а отказ доставки останавливает цепочку и метит контакт.
            </Text>
          </Stack>

          <ParseCalibration />
          <LeadsExport />
        </Stack>
      </Card>

      <Card className="glass tableCard" p="md">
        <Stack gap="sm">
          <SegmentedControl
            aria-label="Вкладки диалогов"
            fullWidth={narrow}
            value={tab}
            onChange={(value) => {
              const next = THREAD_TAB_KEYS.find((key) => key === value);
              if (next !== undefined && next !== tab) switchTab(next);
            }}
            data={THREAD_TAB_KEYS.map((key) => ({
              value: key,
              label: tabLabel(key, tabCounts[key]),
            }))}
          />
          {tab === 'unbound' ? (
            <UnboundReplies page={page} onPage={goToPage} />
          ) : (
            <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
              <Table
                className="dataTable fixedTable filteredTable"
                layout="fixed"
                tabularNums
                verticalSpacing="sm"
                horizontalSpacing="md"
              >
                <colgroup>
                  {COLUMNS.map((column) => (
                    <col
                      key={column.title}
                      style={column.width ? { width: column.width } : undefined}
                    />
                  ))}
                </colgroup>
                <Table.Thead>
                  <Table.Tr>
                    {COLUMNS.map((column) => (
                      <Table.Th key={column.title}>{column.title}</Table.Th>
                    ))}
                  </Table.Tr>
                  <Table.Tr className="filterRow">
                    <Table.Th>
                      <TextInput
                        size="xs"
                        placeholder="Донор или адрес"
                        aria-label="Поиск по донору или адресу"
                        value={search}
                        onChange={(event) => setSearch(event.currentTarget.value)}
                      />
                    </Table.Th>
                    <Table.Th>
                      <Select
                        size="xs"
                        aria-label="Состояние"
                        allowDeselect={false}
                        value={filters.state ?? 'all'}
                        onChange={(value) =>
                          apply({
                            state: THREAD_STATE_KEYS.find((state) => state === value) ?? null,
                          })
                        }
                        data={[
                          { value: 'all', label: counted('все', threads.length) },
                          ...states.map((state) => ({
                            value: state,
                            label: counted(threadState(state).title, counts.get(state) ?? 0),
                          })),
                        ]}
                      />
                    </Table.Th>
                    <Table.Th />
                    <Table.Th />
                    <Table.Th />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {empty !== null && (
                    <WholeRow>
                      <Stack gap={6} align="flex-start" py="sm">
                        <Text size="sm" fw={500}>
                          {empty.title}
                        </Text>
                        <Text size="sm" c="dimmed">
                          {empty.detail}
                        </Text>
                        {isThreadFiltered(typed) && (
                          <Button
                            variant="subtle"
                            size="compact-sm"
                            className="press"
                            onClick={() => {
                              setSearch('');
                              setParams(writeThreadFilters(NO_THREAD_FILTERS), { replace: true });
                            }}
                          >
                            Сбросить фильтры
                          </Button>
                        )}
                      </Stack>
                    </WholeRow>
                  )}
                  {shown.map((thread) => (
                    <Table.Tr
                      key={thread.id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => open(thread)}
                    >
                      <Table.Td>
                        {/* Ссылка, а не только строка: с клавиатуры до строки не
                        дойти, а диалог открывают и в новой вкладке. */}
                        <Anchor
                          component={Link}
                          to={`/threads/${thread.id}`}
                          state={{ from: location.search }}
                          fw={500}
                          c="var(--ink)"
                          underline="hover"
                          className="cellName"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <Seams text={thread.host} />
                        </Anchor>
                        <Text size="xs" c="dimmed" className="cellName">
                          {thread.contact_email ?? 'адрес не определён'}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Badge variant="light" color={threadState(thread.state).color}>
                          {threadState(thread.state).title}
                        </Badge>
                      </Table.Td>
                      <Table.Td>{formatNumber(thread.messages_sent)}</Table.Td>
                      <Table.Td>
                        <PriceCell thread={thread} />
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm" c="dimmed">
                          {formatDateTime(thread.last_event_at)}
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
        </Stack>
      </Card>
    </Stack>
  );
}

/**
 * Лиды файлом — ответы людей на оффер рекламодателю, те же поля, что уходят
 * вебхуком в CRM. Без настроенного вебхука это и есть путь передачи лида.
 */
function LeadsExport() {
  const { can } = useSession();
  const leads = useMutation({
    mutationFn: exportLeads,
    onSuccess: (file) => {
      saveFile(file, 'leads.csv');
      const rows = exportCounts(file).rows;
      const truncated = file.headers.get('X-Export-Truncated') === '1';
      notifications.show({
        message:
          rows === 0
            ? 'Лидов пока нет — файл пустой'
            : truncated
              ? `Выгружено лидов: ${rows ?? '—'} — это потолок, часть лидов не вошла`
              : `Выгружено лидов: ${rows ?? '—'}`,
        ...(truncated ? { color: 'yellow' } : {}),
      });
    },
    onError: (failure) =>
      notifications.show({
        title: 'Выгрузка не удалась',
        message: refusalOf(failure),
        color: 'red',
      }),
  });
  // Файл отдаётся под правом prices — без него кнопки нет, а не отказ после нажатия.
  if (!can('prices')) return null;
  return (
    <Group>
      <Button variant="light" size="xs" loading={leads.isPending} onClick={() => leads.mutate()}>
        Выгрузить лиды
      </Button>
    </Group>
  );
}
