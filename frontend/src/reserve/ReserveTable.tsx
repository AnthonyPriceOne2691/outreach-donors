/**
 * Таблица резерва: домен, почему не прошёл, прогон, DR, трафик, место страны прогона и
 * «Взять на рассмотрение» — или почему нельзя.
 *
 * **Фильтр — под своей колонкой** (`docs/UI_RULES.md`, «Таблицы»): группа причины — под
 * «Почему не прошёл», страна прогона — под «Прогоном», страна в топ-5 — под «Гео».
 * Счётчики — в пунктах списка, в поле — название.
 *
 * **Почему нельзя — словами сервера, на месте кнопки**: «Не берём: кому не пишем» (что
 * именно — в причине рядом), «уже донор — принят человеком», «ждёт решения в очереди
 * прогона №31».
 * Отказ — до нажатия, тем же правилом, что у сервера (одно правило — `review/reserve.py`).
 *
 * **Гео — место страны прогона**: «Канада — 2-я · 18%», «Канада не в топ-5» и верхняя
 * страна второй строкой. У отсеянного по метрикам стран не спрашивали — так и сказано:
 * отказ «по региону» у него не проверяли.
 */

import { Anchor, Button, Group, Select, Stack, Table, Text } from '@mantine/core';
import { Link } from 'react-router-dom';

import { countryTitle } from '../api/labels';
import { DataTable } from '../components/DataTable';
import type { Column } from '../components/ColumnsHead';
import { Seams } from '../components/Seams';
import { SiteLink } from '../components/SiteLink';
import { NoRows } from '../components/WholeRow';
import type { Emptiness } from '../components/WholeRow';
import { formatCompact, formatNumber } from '../format';
import { RESERVE_GROUPS } from './reserveApi';
import type { ReserveCard, ReserveView } from './reserveApi';
import { groupOf } from './reserveFilters';
import type { ReserveFilters } from './reserveFilters';
import { GROUP_TITLES, placeWords, topWords } from './reserveWords';

/** Колонки слева направо; домену — остаток (на 1440 с раскрытым меню — ~190 px, как у
 *  доноров). Текстовые колонки — причина, гео, действие — переносятся (`wrapCell`): их
 *  длина ничем не ограничена. Ширины остальных — по самому длинному: прогон — «№1234» и
 *  «Саудовская Аравия» в две строки; трафик — «1 000 000 000»; действие — кнопка «Взять
 *  на рассмотрение» с полями ячейки (на 13rem она резалась до «…рассмотрени»). */
const COLUMNS: Column[] = [
  { title: 'Домен' },
  { title: 'Почему не прошёл', width: '13rem' },
  { title: 'Прогон', width: '6rem' },
  { title: 'DR', width: '4rem' },
  { title: 'Трафик', width: '6.5rem' },
  { title: 'Гео', width: '11.5rem' },
  { title: 'Действие', width: '14rem' },
];

/** Уже этого таблица уезжает в прокрутку: колонкам — их ширины, домену — 11rem (как у
 *  доноров, `DonorsTable.TABLE_MIN_WIDTH`). */
const MIN_WIDTH = 1056;

/** Стрелка списка в поле фильтра — уже умолчания, как у доноров. */
const ARROW_WIDTH = 22;

function counted(title: string, count: number | undefined): string {
  return count === undefined ? title : `${title} · ${formatNumber(count)}`;
}

/** Страны фильтра — частые сверху; страна из адреса, которой нет в счётчиках, — тоже
 *  пункт: иначе поле показало бы «все», а таблица была бы сужена. */
function countryOptions(counts: Record<string, number> | undefined, chosen: string | null) {
  const entries = Object.entries(counts ?? {});
  entries.sort(
    ([a, left], [b, right]) => right - left || countryTitle(a).localeCompare(countryTitle(b), 'ru'),
  );
  const options = entries.map(([code]) => ({ value: code, label: countryTitle(code) }));
  if (chosen !== null && !options.some((option) => option.value === chosen)) {
    options.push({ value: chosen, label: countryTitle(chosen) });
  }
  return [{ value: 'all', label: 'все' }, ...options];
}

interface FilterProps {
  filters: ReserveFilters;
  view: ReserveView | undefined;
  onFilter: (patch: Partial<ReserveFilters>) => void;
}

function FilterRow({ filters, view, onFilter }: FilterProps) {
  const total = view === undefined ? undefined : view.total;
  return (
    <Table.Tr className="filterRow">
      <Table.Th />
      <Table.Th data-label="Почему не прошёл">
        <Select
          size="xs"
          aria-label="Почему не прошёл"
          rightSectionWidth={ARROW_WIDTH}
          allowDeselect={false}
          value={filters.group ?? 'all'}
          onChange={(value) => onFilter({ group: groupOf(value) })}
          data={[
            { value: 'all', label: 'все' },
            ...RESERVE_GROUPS.map((group) => ({ value: group, label: GROUP_TITLES[group] })),
          ]}
          renderOption={({ option }) => (
            <span>
              {counted(
                option.label,
                option.value === 'all'
                  ? undefined
                  : view?.groups[option.value as keyof typeof GROUP_TITLES],
              )}
            </span>
          )}
        />
      </Table.Th>
      <Table.Th data-label="Страна прогона">
        {filters.run === null ? (
          <Select
            size="xs"
            aria-label="Страна прогона"
            rightSectionWidth={ARROW_WIDTH}
            searchable
            allowDeselect={false}
            nothingFoundMessage="Прогонов этой страны в резерве нет"
            value={filters.country ?? 'all'}
            onChange={(value) =>
              onFilter({ country: value === null || value === 'all' ? null : value })
            }
            data={countryOptions(view?.countries, filters.country)}
            renderOption={({ option }) => (
              <span>
                {counted(
                  option.label,
                  option.value === 'all' ? total : view?.countries[option.value],
                )}
              </span>
            )}
          />
        ) : null}
      </Table.Th>
      <Table.Th />
      <Table.Th />
      <Table.Th data-label="Страна в топ-5">
        <Select
          size="xs"
          aria-label="Страна в топ-5 домена"
          rightSectionWidth={ARROW_WIDTH}
          searchable
          allowDeselect={false}
          nothingFoundMessage="Такой страны нет в топ-5 доменов резерва"
          value={filters.geo ?? 'all'}
          onChange={(value) => onFilter({ geo: value === null || value === 'all' ? null : value })}
          data={countryOptions(view?.geos, filters.geo)}
          renderOption={({ option }) => (
            <span>
              {counted(option.label, option.value === 'all' ? undefined : view?.geos[option.value])}
            </span>
          )}
        />
      </Table.Th>
      <Table.Th />
    </Table.Tr>
  );
}

/** Имя — ссылкой на сайт, как в очереди рассмотрения: резерв — тот же список «посмотреть сайт
 *  и решить» (`SiteLink`). Домен, который гейт снял до Ahrefs, донором не стал — имя текстом. */
function Host({ row }: { row: ReserveCard }) {
  if (row.donor_id !== null) return <SiteLink host={row.host} />;
  return (
    <Text size="sm" fw={500} className="cellName">
      <Seams text={row.host} />
    </Text>
  );
}

interface RowProps {
  row: ReserveCard;
  mayTake: boolean;
  busy: boolean;
  taking: boolean;
  onTake: (row: ReserveCard) => void;
}

function Action({ row, mayTake, busy, taking, onTake }: RowProps) {
  if (!row.takeable) {
    return (
      <Text size="xs" c="dimmed">
        {row.refusal}
      </Text>
    );
  }
  if (!mayTake) {
    return (
      <Text size="xs" c="dimmed">
        Взять может тот, кто решает в очереди: нужно право «подтверждать цены».
      </Text>
    );
  }
  return (
    <Button
      size="compact-sm"
      variant="default"
      className="press"
      disabled={busy}
      loading={taking}
      onClick={() => onTake(row)}
    >
      Взять на рассмотрение
    </Button>
  );
}

function Row(props: RowProps) {
  const { row } = props;
  const top = topWords(row.fit, row.country);
  return (
    <Table.Tr data-group={row.group}>
      <Table.Td>
        <Host row={row} />
      </Table.Td>
      <Table.Td data-label="Почему не прошёл" className="wrapCell">
        <Text size="sm">{row.reason ?? 'причина не записана'}</Text>
      </Table.Td>
      <Table.Td data-label="Прогон">
        <Stack gap={0} align="center">
          <Anchor
            component={Link}
            to={`/runs/${row.run_id}`}
            size="sm"
            c="var(--ink)"
            underline="always"
          >
            №{row.run_id}
          </Anchor>
          <Text size="xs" c="dimmed">
            {countryTitle(row.country)}
          </Text>
        </Stack>
      </Table.Td>
      <Table.Td data-label="DR">{row.dr === null ? '—' : formatNumber(row.dr)}</Table.Td>
      <Table.Td data-label="Трафик">
        {/* Как у доноров и в очереди: «12 тыс.», а не «12 345». */}
        {formatCompact(row.org_traffic)}
      </Table.Td>
      <Table.Td data-label="Гео" className="wrapCell">
        <Stack gap={0} align="center">
          <Text size="sm">{placeWords(row.fit, row.country)}</Text>
          {top !== null && (
            <Text size="xs" c="dimmed">
              {top}
            </Text>
          )}
        </Stack>
      </Table.Td>
      <Table.Td className="cellDecide wrapCell">
        <Group justify="center">
          <Action {...props} />
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}

interface Props {
  view: ReserveView | undefined;
  filters: ReserveFilters;
  stale: boolean;
  refusal: string | null;
  empty: Emptiness | null;
  mayTake: boolean;
  busy: boolean;
  /** Строка, которую берут сейчас: кнопка — с крутилкой. */
  taking: ReserveCard | null;
  onFilter: (patch: Partial<ReserveFilters>) => void;
  onReset: () => void;
  onTake: (row: ReserveCard) => void;
}

export function ReserveTable(props: Props) {
  const { view, filters, taking } = props;
  const rows = props.refusal === null ? (view?.rows ?? []) : [];
  return (
    <DataTable
      columns={COLUMNS}
      minWidth={MIN_WIDTH}
      className="filteredTable"
      stale={props.stale}
      head={<FilterRow filters={filters} view={view} onFilter={props.onFilter} />}
    >
      {rows.map((row) => (
        <Row
          key={`${row.run_id}-${row.domain_id}`}
          row={row}
          mayTake={props.mayTake}
          busy={props.busy || props.stale}
          taking={
            taking !== null && taking.run_id === row.run_id && taking.domain_id === row.domain_id
          }
          onTake={props.onTake}
        />
      ))}
      <NoRows
        span={COLUMNS.length}
        refusal={props.refusal}
        failTitle="Резерв не загрузился"
        empty={rows.length === 0 ? props.empty : null}
        onReset={props.onReset}
      />
    </DataTable>
  );
}
