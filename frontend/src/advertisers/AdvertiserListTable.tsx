/**
 * Таблица «Все рекламодатели» с фильтрами в шапке — каждый под своей колонкой,
 * как у доноров (`donors/DonorsTable.tsx`): что сужает колонку, видно, не читая
 * подписей; на телефоне строка — карточкой, фильтры — столбиком с подписями.
 *
 * **Строка — то, о чём спрашивает сверка ТЗ** (11.10.2026): домен, донор, балл и за что
 * он («найденные рекламодатели со скорингом и причиной»), страница и анкор найденной
 * ссылки, есть ли адрес, что с письмом. У бизнеса ниши ссылки и балла нет — вместо донора
 * сказано, откуда он: «бизнес ниши, прогон №N». Остаток ширины — «За что», как в очереди
 * проверки: причины по строке на причину.
 *
 * **Донор в строке — лучшей ссылки, фильтр — любой**: под лучшую ссылку пишется письмо,
 * а найти рекламодателя по донору нужно у любого, кто на него ссылается, — как считает
 * приёмка. Это сказано в «i» у заголовка колонки.
 *
 * **Адрес — строкой в базе**: «адрес найден», «только форма»; без адреса — что сказал
 * последний поиск («не искали», «сайт не ответил»), теми же словами, что у доноров.
 */

import { Anchor, Badge, Select, Stack, Table, Text, TextInput } from '@mantine/core';

import type { AdvertiserCard, AdvertiserFacets } from '../api/advertiserList';
import { CONTACT_STATUSES, NOT_SEARCHED } from '../api/labels';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { InfoHint } from '../components/InfoHint';
import { Seams } from '../components/Seams';
import { NoRows } from '../components/WholeRow';
import type { Emptiness } from '../components/WholeRow';
import { formatNumber } from '../format';
import { LinkAnchor } from './LinkAnchor';
import {
  ADDRESS_KEYS,
  ADDRESS_STATES,
  OFFER_KEYS,
  OFFER_STATES,
  SOURCE_KEYS,
  SOURCES,
} from './listFilters';
import type { ListFilters } from './listFilters';

/** «i» у «Донора»: в строке — донор лучшей ссылки, фильтр — любой, кто ссылается. */
function DonorHint() {
  return (
    <InfoHint name="Какой донор в строке и в фильтре" width={300}>
      В строке — донор лучшей ссылки: под неё пишется письмо. Фильтр находит рекламодателя у любого
      донора, который на него ссылается, — так же, как его считает приёмка.
    </InfoHint>
  );
}

/** Колонки слева направо — как в очереди проверки: домену 13rem (переносится по швам),
 *  донору 11rem, баллу — значок и «после пересчёта — 3» под ним, остаток — «За что»,
 *  ссылке 12rem; адресу — значок «предел запросов» и поле «только форма», письму —
 *  значок «не собрано» и поле «отписался». */
const COLUMNS: Column[] = [
  { title: 'Рекламодатель', width: '13rem' },
  { title: 'Донор', width: '11rem', hint: <DonorHint /> },
  { title: 'Балл', width: '6.5rem' },
  { title: 'За что' },
  { title: 'Ссылка', width: '12rem' },
  { title: 'Адрес', width: '9.5rem' },
  { title: 'Письмо', width: '9rem' },
];

/** Уже — прокрутка: колонкам с шириной 61rem (976 px), причинам — от 220. */
export const LIST_MIN_WIDTH = 1200;

/** Стрелка списка в поле фильтра — уже умолчания, как у доноров. */
const ARROW_WIDTH = 22;

/** «ушло · 12»: счётчик — в пункте списка, в поле — только слово. */
function counted(title: string, count: number | undefined): string {
  return count === undefined ? title : `${title} · ${formatNumber(count)}`;
}

function options<T extends string>(keys: readonly T[], title: (key: T) => string) {
  return [
    { value: 'all', label: 'все' },
    ...keys.map((key) => ({ value: key, label: title(key) })),
  ];
}

interface FilterProps {
  filters: ListFilters;
  facets: AdvertiserFacets | null;
  /** Поиск — как набран сейчас: в адрес он уходит после паузы в наборе. */
  search: string;
  onSearch: (value: string) => void;
  onFilter: (patch: Partial<ListFilters>) => void;
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
  count: (key: T | 'all') => number | undefined;
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
        <span>{counted(option.label, count(option.value as T | 'all'))}</span>
      )}
    />
  );
}

function FilterRow({ filters, facets, search, onSearch, onFilter }: FilterProps) {
  const all =
    facets === null ? undefined : Object.values(facets.sources).reduce((sum, n) => sum + n, 0);
  const donors = Object.entries(facets?.donors ?? {}).sort(
    ([a, left], [b, right]) => right - left || a.localeCompare(b),
  );
  // Донор из адреса, которого в счётчиках нет, — всё равно вариант: иначе поле
  // показало бы «все», а список был бы сужен до него.
  if (filters.donor !== null && !donors.some(([host]) => host === filters.donor)) {
    donors.push([filters.donor, 0]);
  }
  const of = <T extends string>(counts: Partial<Record<T, number>> | undefined) => {
    return (key: T | 'all') =>
      facets === null ? undefined : key === 'all' ? all : (counts?.[key] ?? 0);
  };
  return (
    <Table.Tr className="filterRow">
      <Table.Th>
        <TextInput
          size="xs"
          placeholder="Домен"
          aria-label="Поиск по домену рекламодателя"
          value={search}
          onChange={(event) => onSearch(event.currentTarget.value)}
        />
      </Table.Th>
      <Table.Th data-label="Донор">
        {/* Доноров — десятки: поле ищет по имени. */}
        <Select
          size="xs"
          aria-label="Донор"
          rightSectionWidth={ARROW_WIDTH}
          searchable
          allowDeselect={false}
          nothingFoundMessage="Такого донора у рекламодателей нет"
          value={filters.donor ?? 'all'}
          onChange={(value) =>
            onFilter({ donor: value === null || value === 'all' ? null : value })
          }
          data={[
            { value: 'all', label: 'все' },
            ...donors.map(([host]) => ({ value: host, label: host })),
          ]}
          renderOption={({ option }) => (
            <span>
              {counted(
                option.label,
                facets === null
                  ? undefined
                  : option.value === 'all'
                    ? all
                    : (facets.donors[option.value] ?? 0),
              )}
            </span>
          )}
        />
      </Table.Th>
      <Table.Th />
      <Table.Th />
      <Table.Th data-label="Откуда">
        <FilterSelect
          label="Откуда"
          value={filters.source}
          keys={SOURCE_KEYS}
          title={(key) => SOURCES[key]}
          count={of(facets?.sources)}
          onPick={(source) => onFilter({ source })}
        />
      </Table.Th>
      <Table.Th data-label="Адрес">
        <FilterSelect
          label="Адрес"
          value={filters.address}
          keys={ADDRESS_KEYS}
          title={(key) => ADDRESS_STATES[key]}
          count={of(facets?.addresses)}
          onPick={(address) => onFilter({ address })}
        />
      </Table.Th>
      <Table.Th data-label="Письмо">
        <FilterSelect
          label="Письмо"
          value={filters.letter}
          keys={OFFER_KEYS}
          title={(key) => OFFER_STATES[key].title}
          count={of(facets?.letters)}
          onPick={(letter) => onFilter({ letter })}
        />
      </Table.Th>
    </Table.Tr>
  );
}

/** Адрес строки: в базе — «адрес найден» или «только форма»; без адреса — исход поиска. */
export function addressBadge(row: AdvertiserCard): { title: string; color: string } {
  if (row.address === 'found') return CONTACT_STATUSES.found;
  if (row.address === 'form') return CONTACT_STATUSES.form_only;
  if (row.contact_status === null) return NOT_SEARCHED;
  // Исход поиска называет адрес или форму, которых в базе уже нет.
  if (row.contact_status === 'found' || row.contact_status === 'form_only') {
    return CONTACT_STATUSES.not_found;
  }
  return CONTACT_STATUSES[row.contact_status];
}

function Where({ row }: { row: AdvertiserCard }) {
  if (row.source === 'niche') {
    return (
      <Text size="sm">
        бизнес ниши
        {row.run_id !== null ? (
          <Text component="span" size="xs" c="dimmed" display="block">
            прогон №{row.run_id}
          </Text>
        ) : null}
      </Text>
    );
  }
  return (
    <>
      <Seams text={row.donor_host ?? '—'} />
      {row.donors > 1 ? (
        <Text size="xs" c="dimmed">
          доноров: {formatNumber(row.donors)}
        </Text>
      ) : null}
    </>
  );
}

/** Балл, с которым перевели, и — если веса с тех пор пересчитали — нынешний под ним:
 *  причины в строке — нынешние. У бизнеса ниши балла нет. */
function Points({ row }: { row: AdvertiserCard }) {
  if (row.source === 'niche') return <>—</>;
  return (
    <Stack gap={2} align="center">
      <Badge variant="light">{row.points}</Badge>
      {row.rescored !== null ? (
        <Text size="xs" c="dimmed">
          после пересчёта — {row.rescored}
        </Text>
      ) : null}
    </Stack>
  );
}

function Row({ row }: { row: AdvertiserCard }) {
  const address = addressBadge(row);
  const letter = OFFER_STATES[row.letter];
  return (
    <Table.Tr>
      <Table.Td>
        {/* Имя — чернилами, как во всех таблицах: бирюзовая ссылка на бирюзовом углу
            полотна не держит норму. */}
        <Anchor
          href={`https://${row.host}`}
          target="_blank"
          rel="noreferrer"
          c="var(--ink)"
          fw={500}
          underline="hover"
          className="cellName"
        >
          <Seams text={row.host} />
        </Anchor>
      </Table.Td>
      <Table.Td className="wrapCell cellName" data-label="Донор">
        <Where row={row} />
      </Table.Td>
      <Table.Td data-label="Балл">
        <Points row={row} />
      </Table.Td>
      {/* Причины — строками, как в очереди проверки: человек сверяет по ним, а не по числу. */}
      <Table.Td className="wrapCell" data-label="За что">
        {row.reasons.length === 0 ? (
          '—'
        ) : (
          <Stack gap={2}>
            {row.reasons.map((reason) => (
              <Text key={reason} size="xs">
                {reason}
              </Text>
            ))}
          </Stack>
        )}
      </Table.Td>
      <Table.Td className="wrapCell" data-label="Ссылка">
        {row.page_url ? (
          <Anchor href={row.page_url} target="_blank" rel="noreferrer" size="sm">
            страница
          </Anchor>
        ) : (
          '—'
        )}
        {row.anchor ? <LinkAnchor anchor={row.anchor} /> : null}
      </Table.Td>
      <Table.Td data-label="Адрес">
        <Badge variant="light" color={address.color}>
          {address.title}
        </Badge>
      </Table.Td>
      <Table.Td data-label="Письмо">
        <Badge variant="light" color={letter.color}>
          {letter.title}
        </Badge>
      </Table.Td>
    </Table.Tr>
  );
}

interface Props extends FilterProps {
  rows: AdvertiserCard[];
  stale: boolean;
  refusal: string | null;
  empty: Emptiness | null;
  onReset: () => void;
}

export function AdvertiserListTable({ rows, stale, refusal, empty, onReset, ...filters }: Props) {
  // Заголовки и фильтры — одной шапкой: строка фильтров встаёт под свои колонки.
  return (
    <DataTable
      columns={COLUMNS}
      minWidth={LIST_MIN_WIDTH}
      className="filteredTable"
      head={<FilterRow {...filters} />}
      stale={stale}
    >
      <NoRows
        span={COLUMNS.length}
        refusal={refusal}
        failTitle="Рекламодатели не загрузились"
        empty={empty}
        onReset={onReset}
      />
      {refusal === null ? rows.map((row) => <Row key={row.id} row={row} />) : null}
    </DataTable>
  );
}
