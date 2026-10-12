/**
 * Строка фильтров таблицы доноров — под каждой колонкой свой (замечание 26.09.2026).
 *
 * Отдельным файлом с 12.10.2026: таблица получила цену под доменом, решение человека
 * и порядок (сверка ТЗ), и `DonorsTable.tsx` упёрся в свой потолок. Правила те же:
 * фильтр живёт под колонкой, которую сужает, счётчики — в пунктах списка, в поле —
 * название. Фильтр цены — над таблицей (`DonorsScope.tsx`): своей колонки у цены нет.
 */

import { Select, Table, TextInput } from '@mantine/core';

import { countryTitle, DONOR_FRESHNESS, DONOR_STATUSES } from '../api/labels';
import type { DonorFreshness, DonorStatus } from '../api/types';
import { NumberField } from '../components/NumberField';
import { formatNumber } from '../format';
import { DR_MAX, FRESHNESS, thresholdRefusal, totalOf, TRAFFIC_MAX } from './donorFilters';
import type { DonorFilters } from './donorFilters';

/** Стрелка списка в поле фильтра — уже умолчания (28 px): на 1440 px шесть
 *  пикселей на поле — это место, которого не хватало колонке домена. */
const ARROW_WIDTH = 22;

const STATUSES = Object.keys(DONOR_STATUSES) as DonorStatus[];

const ADDRESS_OPTIONS = [
  { value: 'any', label: 'все' },
  // Слово то же, что у плитки «С адресом» на главной, и правило то же, что у неё
  // и у ступени «с адресом» на экране писем: адрес есть в базе — число в колонке
  // больше нуля, — а не исход поиска (проверка прода 10.10.2026).
  { value: 'yes', label: 'с адресом' },
  { value: 'no', label: 'без адреса' },
];

function addressValue(address: boolean | null): string {
  if (address === null) return 'any';
  return address ? 'yes' : 'no';
}

/** «Не подходит · 225»: точка, как у «США · 62%», — длинное тире не влезало
 *  в поле колонки вместе с четырёхзначным числом. */
function counted(title: string, count: number | undefined): string {
  return count === undefined ? title : `${title} · ${formatNumber(count)}`;
}

/** Сводка сервера для вариантов фильтров. `null` — ещё не пришла: подписи
 *  без чисел, а не с нулями. */
export interface Facets {
  counts: Record<string, number>;
  countries: Record<string, number>;
  freshness: Partial<Record<DonorFreshness, number>>;
}

/** Страны — какие есть у доноров: частые сверху, при равенстве — по имени.
 *  Подпись — только название: по нему в поле ищут, а счётчик рисует список. */
function countryOptions(facets: Facets | null): { value: string; label: string }[] {
  const countries = Object.entries(facets?.countries ?? {});
  countries.sort(
    ([a, left], [b, right]) => right - left || countryTitle(a).localeCompare(countryTitle(b), 'ru'),
  );
  return countries.map(([code]) => ({ value: code, label: countryTitle(code) }));
}

export interface FilterRowProps {
  filters: DonorFilters;
  facets: Facets | null;
  /** Поиск и пороги — как набраны сейчас: в адрес они уходят после паузы,
   *  порог — только годным числом. */
  search: string;
  onSearch: (value: string) => void;
  minDr: string;
  onMinDr: (text: string) => void;
  minTraffic: string;
  onMinTraffic: (text: string) => void;
  onFilter: (patch: Partial<DonorFilters>) => void;
}

export function FilterRow({
  filters,
  facets,
  search,
  onSearch,
  minDr,
  onMinDr,
  minTraffic,
  onMinTraffic,
  onFilter,
}: FilterRowProps) {
  const all = facets === null ? undefined : totalOf(facets.counts);
  const geo = filters.geo;
  const countries = countryOptions(facets);
  // Страна из адреса, которой у доноров нет, — всё равно вариант: иначе
  // поле показало бы «все», а таблица была бы сужена до неё.
  if (geo !== null && !countries.some((option) => option.value === geo)) {
    countries.push({ value: geo, label: countryTitle(geo) });
  }
  const countryCount = (code: string) =>
    facets === null ? undefined : code === 'all' ? all : (facets.countries[code] ?? 0);
  return (
    <Table.Tr className="filterRow">
      <Table.Th />
      <Table.Th>
        {/* Без значка лупы: на ноутбуке колонка домена узкая, и значок съедал
            ровно те пиксели, которых не хватало подсказке. Что это поиск,
            говорит сама подсказка. */}
        <TextInput
          size="xs"
          placeholder="Домен или причина"
          aria-label="Поиск по домену или причине отсева"
          value={search}
          onChange={(event) => onSearch(event.currentTarget.value)}
        />
      </Table.Th>
      <Table.Th>
        <Select
          size="xs"
          aria-label="Вердикт"
          rightSectionWidth={ARROW_WIDTH}
          allowDeselect={false}
          value={filters.status ?? 'all'}
          onChange={(value) =>
            onFilter({ status: STATUSES.find((status) => status === value) ?? null })
          }
          data={[
            { value: 'all', label: 'все' },
            ...STATUSES.map((status) => ({ value: status, label: DONOR_STATUSES[status].title })),
          ]}
          renderOption={({ option }) => (
            <span>
              {counted(
                option.label,
                option.value === 'all'
                  ? all
                  : facets === null
                    ? undefined
                    : (facets.counts[option.value] ?? 0),
              )}
            </span>
          )}
        />
      </Table.Th>
      <Table.Th>
        {/* Порог — как набран: «1.5» не склеивается в 15, под полем — почему
            так нельзя, а фильтр прежний (проверка QA 10.10.2026). */}
        <NumberField
          size="xs"
          placeholder="не ниже"
          aria-label="DR не ниже"
          value={minDr}
          error={thresholdRefusal(minDr, DR_MAX)}
          onChange={onMinDr}
        />
      </Table.Th>
      <Table.Th>
        {/* Разряды — пробелом, как у чисел на экране: «1 000 000» читается,
            «1000000» — пересчитывается по нулям. */}
        <NumberField
          size="xs"
          placeholder="не ниже"
          aria-label="Трафик не ниже"
          grouped
          value={minTraffic}
          error={thresholdRefusal(minTraffic, TRAFFIC_MAX)}
          onChange={onMinTraffic}
        />
      </Table.Th>
      <Table.Th>
        {/* Стран может быть много — поле ищет по названию: «герм» находит
            «Германия · 32». */}
        <Select
          size="xs"
          aria-label="Гео"
          rightSectionWidth={ARROW_WIDTH}
          searchable
          allowDeselect={false}
          nothingFoundMessage="Такой страны у доноров нет"
          value={geo ?? 'all'}
          onChange={(value) => onFilter({ geo: value === null || value === 'all' ? null : value })}
          data={[{ value: 'all', label: 'все' }, ...countries]}
          renderOption={({ option }) => (
            <span>{counted(option.label, countryCount(option.value))}</span>
          )}
        />
      </Table.Th>
      <Table.Th>
        <Select
          size="xs"
          aria-label="Адреса"
          rightSectionWidth={ARROW_WIDTH}
          allowDeselect={false}
          value={addressValue(filters.address)}
          onChange={(value) =>
            onFilter({ address: value === 'yes' ? true : value === 'no' ? false : null })
          }
          data={ADDRESS_OPTIONS}
        />
      </Table.Th>
      <Table.Th>
        <Select
          size="xs"
          aria-label="Данные"
          rightSectionWidth={ARROW_WIDTH}
          allowDeselect={false}
          value={filters.freshness ?? 'all'}
          onChange={(value) =>
            onFilter({ freshness: FRESHNESS.find((state) => state === value) ?? null })
          }
          data={[
            { value: 'all', label: 'все' },
            ...FRESHNESS.map((state) => ({ value: state, label: DONOR_FRESHNESS[state].title })),
          ]}
          renderOption={({ option }) => (
            <span>
              {counted(
                option.label,
                option.value === 'all'
                  ? all
                  : facets === null
                    ? undefined
                    : (facets.freshness[option.value as DonorFreshness] ?? 0),
              )}
            </span>
          )}
        />
      </Table.Th>
    </Table.Tr>
  );
}
