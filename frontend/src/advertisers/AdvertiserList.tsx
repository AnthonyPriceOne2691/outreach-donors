/**
 * «Все рекламодатели»: список с фильтрами под колонками, страницы и выгрузка файлом.
 *
 * Сверка ТЗ 11.10.2026: рекламодателей было не увидеть списком — очередь проверки
 * показывает кандидатов, «К письму» — числа, а выгрузка файлом была только у лидов.
 * В списке — те, кому пишем или напишем: по найденной ссылке — всякий, бизнес ниши —
 * после «Пишем» (`crawl/advertiser_list.in_work`).
 *
 * **Фильтры и страница — в адресе** (`listFilters.ts`), по общему правилу списков
 * (`components/useUrlFilters.ts`): смена фильтра — первая страница и замена записи
 * в истории, страница — новая запись. Прежние строки стоят приглушёнными, пока едут новые.
 *
 * **Кнопка выгрузки одна**, и её подпись говорит, что ляжет в файл: всех или
 * найденных, и не больше потолка сервера (`donors/exporting.exportPlan`).
 */

import { Button, Card, Group, Loader, Stack, Text, Title } from '@mantine/core';
import { useMutation, useQuery } from '@tanstack/react-query';

import {
  exportAdvertisers,
  fetchAdvertiserFacets,
  fetchAdvertiserList,
} from '../api/advertiserList';
import type { AdvertiserFacets, AdvertiserListView } from '../api/advertiserList';
import { refusalOf } from '../api/client';
import { exportCounts, saveFile } from '../api/donors';
import { InfoHint } from '../components/InfoHint';
import { PageSwitch } from '../components/PageSwitch';
import { usePagedQuery, useUrlFilters } from '../components/useUrlFilters';
import { exportPlan } from '../donors/exporting';
import { useTyped } from '../donors/useTyped';
import { formatNumber, plural } from '../format';
import { notify } from '../notices';
import { AdvertiserListTable } from './AdvertiserListTable';
import {
  isListFiltered,
  listEmptiness,
  listQueryOf,
  NO_LIST_FILTERS,
  readListFilters,
  writeListFilters,
} from './listFilters';
import type { ListFilters } from './listFilters';

export const ADVERTISER_LIST_KEY = ['advertisers', 'list'] as const;
export const ADVERTISER_FACETS_KEY = ['advertisers', 'list-facets'] as const;

const ABOUT =
  'Рекламодатели, которым пишем или напишем: найденные по ссылке на нашем доноре — после ' +
  'перевода «куплено» и «пишем», бизнесы ниши — после «Пишем». За что — причины балла той ' +
  'ссылки, под которую письмо. Адрес — есть ли он в базе; без адреса — что сказал последний ' +
  'поиск. Письмо — что с оффером: собрано, ушло, ответил; «не дошло» — тоже ушедшее письмо. ' +
  'Выгрузка — те же строки под тем же фильтром, файлом для Excel.';

/** Набранный поиск совпадает с адресом без пробелов по краям. */
const sameSearch = (draft: string, committed: string) => draft.trim() === committed;

function totalOf(facets: AdvertiserFacets | undefined): number | null {
  if (facets === undefined) return null;
  return Object.values(facets.sources).reduce((sum, count) => sum + count, 0);
}

/** Что легло в файл — словами, по заголовкам ответа, а не по догадке. */
function exported(rows: number | null, asked: number | null): string {
  if (rows === null) return 'Файл выгружен.';
  const said = `${formatNumber(rows)} ${plural(rows, 'рекламодатель', 'рекламодателя', 'рекламодателей')}`;
  if (asked === null || rows >= asked) return `Выгружено: ${said}.`;
  return (
    `В файле ${formatNumber(rows)} из ${formatNumber(asked)}: больше за раз не выгружается — ` +
    'сузьте фильтр, чтобы выгрузить остальных.'
  );
}

/** Страница списка с сервера; страница из старой ссылки за концом — последняя
 *  настоящая, а не пустота. */
function useListPage(filters: ListFilters, turn: (page: number, replace?: boolean) => void) {
  return usePagedQuery(
    [...ADVERTISER_LIST_KEY, writeListFilters(filters).toString()],
    () => fetchAdvertiserList(listQueryOf(filters), filters.page),
    filters.page,
    turn,
  );
}

/** Выгрузка тем же фильтром; что легло в файл — уведомлением по заголовкам ответа. */
function useExport(filters: ListFilters) {
  return useMutation({
    mutationFn: () => exportAdvertisers(listQueryOf(filters)),
    onSuccess: (file) => {
      saveFile(file, 'advertisers.csv');
      const counts = exportCounts(file);
      notify({ message: exported(counts.rows, counts.asked) });
    },
    onError: (failure) =>
      notify({ title: 'Выгрузка не удалась', message: refusalOf(failure), color: 'red' }),
  });
}

/** Почему список пуст — когда ответ улёгся и строк нет; иначе `null`. Всего — по
 *  счётчикам; не пришли — по самой странице. */
function emptinessOf(
  data: AdvertiserListView | undefined,
  facets: AdvertiserFacets | undefined,
  filters: ListFilters,
) {
  if (data === undefined || data.rows.length > 0) return null;
  return listEmptiness(filters, totalOf(facets) ?? data.total);
}

/** Счётчики под фильтрами — своим запросом: они про весь список, и смена страницы или
 *  фильтра их не повторяет; перечитываются вместе с остальным экраном. */
function useFacets() {
  return useQuery({ queryKey: ADVERTISER_FACETS_KEY, queryFn: fetchAdvertiserFacets });
}

/** «всего 12» или «найдено 3 из 12» — у заголовка. */
function foundLine(filtered: boolean, total: number, all: number): string {
  return filtered
    ? `найдено ${formatNumber(total)} из ${formatNumber(all)}`
    : `всего ${formatNumber(all)}`;
}

/** Заголовок с числом и кнопка выгрузки. Пока едет новый ответ, число в подписи
 *  кнопки — от прежнего фильтра, а выгрузка ушла бы с новым: кнопка ждёт ответа. */
function ListHead({
  data,
  facets,
  filtered,
  stale,
  download,
}: {
  data: AdvertiserListView | undefined;
  facets: AdvertiserFacets | undefined;
  filtered: boolean;
  stale: boolean;
  download: ReturnType<typeof useExport>;
}) {
  const all = totalOf(facets);
  const plan =
    data === undefined
      ? null
      : exportPlan({ picked: 0, filtered, found: data.total, limit: data.export_limit });
  return (
    <Group justify="space-between" align="center" gap="sm">
      <Group gap="sm" align="center" wrap="nowrap">
        <Title order={3}>Все рекламодатели</Title>
        {data !== undefined && all !== null ? (
          <Text size="sm" c="var(--ink)">
            {foundLine(filtered, data.total, all)}
          </Text>
        ) : null}
        <InfoHint name="Кто в списке рекламодателей" width={360}>
          {ABOUT}
        </InfoHint>
      </Group>
      <Button
        variant="default"
        className="press"
        loading={download.isPending}
        disabled={plan === null || plan.empty || stale}
        onClick={() => download.mutate()}
      >
        {plan?.label ?? 'Выгрузить'}
      </Button>
    </Group>
  );
}

export function AdvertiserList() {
  const { filters, apply, turn, reset } = useUrlFilters(readListFilters, writeListFilters);
  const [search, setSearch] = useTyped(
    filters.search,
    (value) => apply({ search: value.trim() }),
    sameSearch,
  );
  const { query, data, settled, pages } = useListPage(filters, turn);
  const facets = useFacets().data;
  const download = useExport(filters);

  if (data === undefined && query.isPending) {
    return <Loader aria-label="Загружаем рекламодателей" m="md" />;
  }

  return (
    <Card className="glassPanel" p="lg">
      <Stack gap="sm">
        <ListHead
          data={data}
          facets={facets}
          filtered={isListFiltered(filters)}
          stale={query.isPlaceholderData}
          download={download}
        />
        <AdvertiserListTable
          rows={data?.rows ?? []}
          stale={query.isPlaceholderData && query.isFetching}
          refusal={query.error ? refusalOf(query.error) : null}
          empty={emptinessOf(settled ? data : undefined, facets, filters)}
          filters={filters}
          facets={facets ?? null}
          search={search}
          onSearch={setSearch}
          onFilter={apply}
          onReset={() => {
            setSearch('');
            reset(NO_LIST_FILTERS);
          }}
        />
        <PageSwitch
          label="Страницы рекламодателей"
          page={filters.page}
          pages={pages}
          onChange={turn}
        />
      </Stack>
    </Card>
  );
}
