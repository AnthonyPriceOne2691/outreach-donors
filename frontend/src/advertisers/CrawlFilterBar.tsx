/**
 * Фильтры вкладки «Обход доноров» — по правилу списков (`docs/UI_RULES.md`): фильтр колонки
 * живёт под её заголовком (`CrawlFilterRow`: домен, откуда цена, срок цены), разрез всего
 * списка — над таблицей: счётчики по статусу последнего обхода кнопками (`StatusFacets`),
 * порядок строк и размер страницы (`CrawlScope`).
 *
 * **Числа у счётчиков — со всеми условиями, кроме своего** (сервер, `donor_page.facets`):
 * выбранный статус не обнуляет соседние кнопки, и видно, сколько станет, если нажать другую.
 * Нулевой статус кнопкой не стоит — кроме выбранного: снять его должно быть чем.
 *
 * **Поиск — после паузы** (`donors/useTyped`): каждая буква — не запрос и не запись в адрес.
 */

import { Button, Group, Select, Table, TextInput } from '@mantine/core';

import type { CrawlCounts, DonorCrawlSort, DonorCrawlStatus, DonorPriceState } from '../api/crawls';
import type { PriceSource } from '../api/manualPrice';
import { Labeled } from '../components/Labeled';
import { useTyped } from '../donors/useTyped';
import { formatNumber } from '../format';
import type { CrawlFilters } from './crawlFilters';
import {
  CRAWL_SORTS,
  CRAWL_STATUSES,
  PRICE_KEYS,
  PRICE_SOURCES,
  PRICE_STATES,
  SIZES,
  SORT_KEYS,
  SOURCE_KEYS,
  STATUS_KEYS,
  statusTotal,
} from './crawlFilters';

type Apply = (patch: Partial<CrawlFilters>) => void;

/** Стрелка списка в поле фильтра — уже умолчания, как у остальных таблиц с фильтрами. */
const ARROW_WIDTH = 22;

/** Счётчики по статусу последнего обхода — кнопками-фильтрами. */
export function StatusFacets({
  counts,
  filters,
  onFilter,
}: {
  counts: CrawlCounts | undefined;
  filters: CrawlFilters;
  onFilter: Apply;
}) {
  const all = counts === undefined ? 0 : statusTotal(counts);
  const shown = STATUS_KEYS.filter(
    (key) => (counts?.status[key] ?? 0) > 0 || key === filters.status,
  );
  const pick = (status: DonorCrawlStatus | null) => onFilter({ status });
  return (
    <Group gap={6} role="group" aria-label="Статус последнего обхода">
      <Button
        size="compact-sm"
        radius="xl"
        variant={filters.status === null ? 'filled' : 'light'}
        color="gray"
        aria-pressed={filters.status === null}
        onClick={() => pick(null)}
      >
        все · {formatNumber(all)}
      </Button>
      {shown.map((key) => {
        const on = filters.status === key;
        return (
          <Button
            key={key}
            size="compact-sm"
            radius="xl"
            variant={on ? 'filled' : 'light'}
            color={CRAWL_STATUSES[key].color}
            aria-pressed={on}
            onClick={() => pick(on ? null : key)}
          >
            {CRAWL_STATUSES[key].title} · {formatNumber(counts?.status[key] ?? 0)}
          </Button>
        );
      })}
    </Group>
  );
}

const SORT_OPTIONS = SORT_KEYS.map((key: DonorCrawlSort) => ({
  value: key,
  label: CRAWL_SORTS[key],
}));
const SIZE_OPTIONS = SIZES.map((size) => ({ value: String(size), label: `по ${size}` }));

/** Порядок строк и размер страницы — разрез всего списка, над таблицей. */
export function CrawlScope({ filters, onFilter }: { filters: CrawlFilters; onFilter: Apply }) {
  return (
    <Group gap="md" wrap="wrap">
      <Labeled label="Порядок">
        <Select
          size="sm"
          w={196}
          aria-label="Порядок строк"
          allowDeselect={false}
          value={filters.sort}
          onChange={(value) =>
            onFilter({ sort: SORT_KEYS.find((key) => key === value) ?? 'priced' })
          }
          data={SORT_OPTIONS}
        />
      </Labeled>
      <Labeled label="На странице">
        <Select
          size="sm"
          w={96}
          aria-label="Доноров на странице"
          allowDeselect={false}
          value={String(filters.size)}
          onChange={(value) =>
            onFilter({ size: SIZES.find((size) => String(size) === value) ?? filters.size })
          }
          data={SIZE_OPTIONS}
        />
      </Labeled>
    </Group>
  );
}

/** «протухла · 120»: счётчик — в пункте списка, в поле — только слово. */
function counted(label: string, count: number | undefined): string {
  return count === undefined ? label : `${label} · ${formatNumber(count)}`;
}

function options<T extends string>(keys: readonly T[], title: (key: T) => string) {
  return [
    { value: 'all', label: 'все' },
    ...keys.map((key) => ({ value: key, label: title(key) })),
  ];
}

function FilterSelect<T extends string>({
  label,
  value,
  keys,
  title,
  count,
  onPick,
}: {
  label: string;
  value: T | null;
  keys: readonly T[];
  title: (key: T) => string;
  count: (key: T) => number | undefined;
  onPick: (value: T | null) => void;
}) {
  return (
    <Select
      size="xs"
      aria-label={label}
      rightSectionWidth={ARROW_WIDTH}
      allowDeselect={false}
      value={value ?? 'all'}
      onChange={(picked) => onPick(keys.find((key) => key === picked) ?? null)}
      data={options(keys, title)}
      renderOption={({ option }) => (
        <span>
          {option.value === 'all' ? option.label : counted(option.label, count(option.value as T))}
        </span>
      )}
    />
  );
}

/** Набранный поиск совпадает с адресом без пробелов по краям. */
const sameSearch = (draft: string, committed: string) => draft.trim() === committed;

/** Фильтры под заголовками колонок: домен, откуда цена, срок цены; у отметки, обхода и ссылок
 *  своего поля нет — статус обхода сужают счётчики над таблицей. */
export function CrawlFilterRow({
  counts,
  filters,
  picks,
  onFilter,
}: {
  counts: CrawlCounts | undefined;
  filters: CrawlFilters;
  /** Есть ли колонка отметки слева. */
  picks: boolean;
  onFilter: Apply;
}) {
  const [search, setSearch] = useTyped(
    filters.search,
    (typed) => onFilter({ search: typed.trim() }),
    sameSearch,
  );
  return (
    <Table.Tr className="filterRow">
      {picks ? <Table.Th /> : null}
      <Table.Th>
        <TextInput
          size="xs"
          placeholder="Домен"
          aria-label="Домен содержит"
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
        />
      </Table.Th>
      <Table.Th data-label="Откуда цена">
        <FilterSelect
          label="Откуда цена"
          value={filters.source}
          keys={SOURCE_KEYS}
          title={(key: PriceSource) => PRICE_SOURCES[key]}
          count={() => undefined}
          onPick={(source) => onFilter({ source })}
        />
      </Table.Th>
      <Table.Th data-label="Срок цены">
        <FilterSelect
          label="Срок цены"
          value={filters.price}
          keys={PRICE_KEYS}
          title={(key: DonorPriceState) => PRICE_STATES[key]}
          count={(key) => counts?.price[key]}
          onPick={(price) => onFilter({ price })}
        />
      </Table.Th>
      <Table.Th />
      <Table.Th />
    </Table.Tr>
  );
}
