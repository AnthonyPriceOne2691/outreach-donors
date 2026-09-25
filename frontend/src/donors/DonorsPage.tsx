/**
 * База доноров: одна панель — шапка, таблица с фильтрами, страницы.
 *
 * **«Не проверен» и «не подходит» — разные состояния**, и в таблице они
 * разного цвета. Спутать их значит копить ложные отказы: домен без данных
 * Ahrefs надо добрать позже, а не закрыть.
 *
 * **Причина отсева показывается всегда.** «Не подходит» без причины —
 * это решение, которое нельзя оспорить, а пороги у нас версионируются
 * именно затем, чтобы прошлые решения объяснялись.
 *
 * **Экран — одна панель** (замечание 25.09.2026). Блоки «Контакты» и
 * «Доноры» над таблицей убраны: адреса и их поиск — в карточке донора,
 * фильтры — в шапке таблицы под своими колонками, общий поиск адресов —
 * строкой «ждут адреса: N · найти», выгрузка — справа в шапке.
 *
 * **По двадцать на страницу, страница и фильтры — в адресе.** Любая смена
 * фильтра возвращает на первую страницу: двадцатая страница старого
 * вопроса ничего не говорит о новом.
 */

import { Box, Button, Card, Group, Loader, Pagination, Stack, Text, Title } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { exportDonors, saveFile } from '../api/donors';
import { listDonors } from '../api/runs';
import type { DonorRowCard } from '../api/types';
import { formatNumber } from '../format';
import { DonorsTable } from './DonorsTable';
import {
  emptinessOf,
  isFiltered,
  NO_FILTERS,
  PAGE_SIZE,
  queryOf,
  readFilters,
  totalOf,
  writeFilters,
} from './donorFilters';
import type { DonorFilters } from './donorFilters';
import { usePendingContacts } from './PendingContacts';
import { useTyped } from './useTyped';

/** Набранный поиск совпадает с адресом без пробелов по краям: пробел
 *  в конце — это ещё набор, а не новый фильтр. */
const sameSearch = (draft: string, committed: string) => draft.trim() === committed;
const sameNumber = (draft: number | null, committed: number | null) => draft === committed;

/** Стрелки переключателя страниц словами — те же, что у истории прогонов. */
const CONTROL_NAMES: Record<string, string> = {
  previous: 'Предыдущая страница',
  next: 'Следующая страница',
  first: 'Первая страница',
  last: 'Последняя страница',
};

export function DonorsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => readFilters(params), [params]);
  const contacts = usePendingContacts();
  // На телефоне переключатель страниц без соседей текущей: с ними девять
  // кнопок не влезали в строку, и «54 ›» уезжали на вторую.
  const phone = useMediaQuery('(max-width: 30em)') === true;

  // Смена фильтра — замена записи в истории, а не новая: «назад» ведёт
  // туда, откуда пришли, а не по буквам поиска. Страница — новая запись.
  const apply = useCallback(
    (patch: Partial<DonorFilters>) =>
      setParams((current) => writeFilters({ ...readFilters(current), ...patch, page: 1 }), {
        replace: true,
      }),
    [setParams],
  );
  const turn = useCallback(
    (page: number) => setParams((current) => writeFilters({ ...readFilters(current), page })),
    [setParams],
  );

  // Поиск и порог DR печатают — в адрес они уходят после паузы в наборе.
  const [search, setSearch] = useTyped(
    filters.search,
    (value) => apply({ search: value.trim() }),
    sameSearch,
  );
  const [minDr, setMinDr] = useTyped(filters.minDr, (value) => apply({ minDr: value }), sameNumber);

  const query = useQuery({
    queryKey: [
      'donors',
      filters.status,
      filters.search,
      filters.minDr,
      filters.address,
      filters.page,
    ],
    queryFn: () =>
      listDonors({
        ...queryOf(filters),
        limit: PAGE_SIZE,
        offset: (filters.page - 1) * PAGE_SIZE,
      }),
    // Пока идёт новая страница, стоит старая: иначе таблица с фильтрами
    // исчезала бы на каждую букву поиска вместе с полем, в котором печатают.
    placeholderData: keepPreviousData,
  });

  const data = query.data;
  const settled = data !== undefined && !query.isPlaceholderData;
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE));

  // Страница из старой ссылки может оказаться за концом: вместо пустоты
  // с «ничего не нашлось» — последняя настоящая страница.
  useEffect(() => {
    if (settled && data.rows.length === 0 && data.total > 0 && filters.page > pages) {
      setParams((current) => writeFilters({ ...readFilters(current), page: pages }), {
        replace: true,
      });
    }
  }, [settled, data, filters.page, pages, setParams]);

  const download = useMutation({
    mutationFn: () => exportDonors(queryOf(filters)),
    onSuccess: (file) => saveFile(file, 'donors.csv'),
    onError: (failure) =>
      notifications.show({
        title: 'Выгрузка не удалась',
        message: refusalOf(failure),
        color: 'red',
      }),
  });

  if (data === undefined && query.isPending) {
    return <Loader aria-label="Загружаем доноров" m="md" />;
  }

  const counts = data?.counts ?? null;
  const all = counts === null ? null : totalOf(counts);
  const open = (donor: DonorRowCard) =>
    void navigate(`/donors/${donor.id}`, { state: { from: location.search } });

  return (
    <Card className="glassPanel" p="lg">
      <Stack gap="sm">
        <Group justify="space-between" align="center" gap="sm">
          <Group gap="sm" align="baseline" wrap="nowrap">
            <Title order={3}>Доноры</Title>
            {data !== undefined && all !== null && (
              // Полными чернилами, как подпись плитки: число стоит в углу
              // панели, на блике стекла, и приглушённый тон там в тёмной
              // теме не держал норму (замер 25.09.2026 по ядру буквы).
              <Text size="sm" c="var(--ink)" className="donorsFound">
                {isFiltered(filters)
                  ? `найдено ${formatNumber(data.total)} из ${formatNumber(all)}`
                  : `всего ${formatNumber(all)}`}
              </Text>
            )}
          </Group>
          <Group gap="md" align="center">
            {contacts.control}
            {/* Выгружается то, что видно: фильтр — часть вопроса, на который
                отвечают файлом. Выгрузка «всего» при включённом фильтре
                не совпала бы с экраном. */}
            <Button
              variant="default"
              className="press"
              loading={download.isPending}
              onClick={() => download.mutate()}
            >
              Выгрузить
            </Button>
          </Group>
        </Group>

        {contacts.outcome !== null && <Stack gap={6}>{contacts.outcome}</Stack>}

        <DonorsTable
          rows={data?.rows ?? []}
          stale={query.isPlaceholderData && query.isFetching}
          refusal={query.error ? refusalOf(query.error) : null}
          empty={settled && data.rows.length === 0 ? emptinessOf(filters, data.counts) : null}
          from={location.search}
          filters={filters}
          counts={counts}
          search={search}
          onSearch={setSearch}
          minDr={minDr}
          onMinDr={setMinDr}
          onFilter={apply}
          onOpen={open}
          onReset={() => setParams(writeFilters(NO_FILTERS), { replace: true })}
        />

        {pages > 1 && (
          // Тот же вид, что у истории прогонов: навигация с именем, номер —
          // в своём элементе (по нему и меряют контраст, а не по кругу кнопки).
          <Box component="nav" aria-label="Страницы доноров">
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
  );
}
