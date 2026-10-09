/**
 * Таблица лидов с фильтрами под заголовками — каждый под своей колонкой.
 *
 * **Фильтр живёт у колонки, которую сужает**, тем же словом, что стоит в её
 * ячейках (правило отбора, 26.09.2026): поиск — под лидом, гипотеза, состояние
 * и причина — под своими колонками. Список причин — из ответа сервера: это
 * его перечень, экран только называет коды словами.
 *
 * **Отказ назван дважды**: значком с причиной (по нему фильтруют) и словами
 * очистки (`cleaning_note`) — что именно нашлось: «дубль: адрес уже у лида №1».
 * Значок причины — `light`, как и значок состояния: у контурного красный текст
 * на светлом стекле намерился 4,23 : 1 при норме 4,5 (мастер, 04.10.2026).
 * Лид, оставшийся новым из-за несработавшей проверки, несёт те же слова:
 * «проверка не выполнена: …» — иначе он неотличим от ещё не очищенного.
 *
 * **Ширины колонок — по самому длинному, что в них бывает**; строка не
 * переносится, узкому окну — прокрутка (`FixedTable`). Первая колонка слева —
 * в ней имя.
 */

import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
} from '@mantine/core';
import { countryTitle } from '../api/labels';
import { LEAD_STATES, leadReasonTitle } from '../api/salesLabels';
import type { HypothesisCard, LeadCard, LeadState } from '../api/salesTypes';
import { Seams } from '../components/Seams';
import { dropdownBelow } from '../theme';
import { FixedTable } from './FixedTable';
import type { Column } from './FixedTable';
import { LEAD_STATE_KEYS, reasonOn } from './leadFilters';
import type { Emptiness, LeadFilters } from './leadFilters';

/** Ширины полей фильтра — по самому длинному значению: «отклонён» — узкое поле,
 *  причина «домен в работе у другого направления» не влезает ни в какое разумное
 *  поле, поэтому её список — по содержимому (`WIDE_LIST`), а поле — по колонке. */
const FIELD = {
  search: '12rem',
  hypothesis: '9rem',
  state: '7.5rem',
  reason: '15rem',
} as const;

const COLUMNS: Column[] = [
  { title: 'Лид', width: '17rem' },
  { title: 'Компания', width: '13rem' },
  { title: 'Гипотеза', width: '10rem' },
  { title: 'Страна', width: '9rem' },
  { title: 'Состояние', width: '8rem' },
  { title: 'Причина', width: '17rem' },
];

/** Уже этого таблица не сжимается и уезжает в прокрутку: сумма ширин (74rem). */
export const TABLE_MIN_WIDTH = 1184;

const ALL = 'all';

/** Список по ширине содержимого, а не поля: длинная причина иначе рвалась бы
 *  на три строки. Левый край — по полю, как у остальных списков. */
const WIDE_LIST = { ...dropdownBelow, width: 'max-content' } as const;

interface Choice {
  value: string;
  label: string;
}

interface FilterProps {
  label: string;
  width: string;
  value: string | null;
  choices: Choice[];
  onChange: (value: string | null) => void;
  wide?: boolean;
}

/** Выбор под колонкой: «все» и значения колонки, поле — по центру ячейки. */
function ColumnFilter({ label, width, value, choices, onChange, wide = false }: FilterProps) {
  const data: Choice[] = [{ value: ALL, label: 'все' }, ...choices];
  const pick = (picked: string | null) =>
    onChange(choices.find((choice) => choice.value === picked)?.value ?? null);
  return (
    <Group justify="center">
      <Select
        size="xs"
        w={width}
        aria-label={label}
        allowDeselect={false}
        value={value ?? ALL}
        data={data}
        onChange={pick}
        comboboxProps={wide ? WIDE_LIST : dropdownBelow}
      />
    </Group>
  );
}

export interface FilterRowProps {
  filters: LeadFilters;
  /** Поиск — как набран сейчас: в адрес он уходит после паузы. */
  search: string;
  onSearch: (value: string) => void;
  onFilter: (patch: Partial<LeadFilters>) => void;
  hypotheses: HypothesisCard[];
  /** Коды причин — порядком и перечнем сервера. */
  reasons: string[];
}

function FilterRow({ filters, search, onSearch, onFilter, hypotheses, reasons }: FilterRowProps) {
  return (
    <Table.Tr className="filterRow">
      <Table.Th>
        <TextInput
          size="xs"
          w={FIELD.search}
          placeholder="Адрес, имя, компания"
          aria-label="Поиск по адресу, имени или компании"
          value={search}
          onChange={(event) => onSearch(event.currentTarget.value)}
        />
      </Table.Th>
      <Table.Th />
      <Table.Th>
        <ColumnFilter
          label="Гипотеза"
          width={FIELD.hypothesis}
          value={filters.hypothesis === null ? null : String(filters.hypothesis)}
          choices={hypotheses.map((row) => ({ value: String(row.id), label: row.name }))}
          onChange={(value) => onFilter({ hypothesis: value === null ? null : Number(value) })}
        />
      </Table.Th>
      <Table.Th />
      <Table.Th>
        <ColumnFilter
          label="Состояние"
          width={FIELD.state}
          value={filters.state}
          choices={LEAD_STATE_KEYS.map((state) => ({
            value: state,
            label: LEAD_STATES[state].title,
          }))}
          onChange={(value) =>
            onFilter({ state: LEAD_STATE_KEYS.find((state) => state === value) ?? null })
          }
        />
      </Table.Th>
      <Table.Th>
        {/* У новых и готовых причины нет по определению: поля нет. */}
        {reasonOn(filters.state) && (
          <ColumnFilter
            label="Причина отказа"
            width={FIELD.reason}
            value={filters.reason}
            choices={reasons.map((code) => ({ value: code, label: leadReasonTitle(code) }))}
            onChange={(value) => onFilter({ reason: value })}
            wide
          />
        )}
      </Table.Th>
    </Table.Tr>
  );
}

function StateBadge({ state }: { state: LeadState }) {
  return (
    <Badge variant="light" color={LEAD_STATES[state].color}>
      {LEAD_STATES[state].title}
    </Badge>
  );
}

/** Что очистка сказала о лиде: причина значком и словами, или вердикт проверки. */
function Outcome({ row }: { row: LeadCard }) {
  const note =
    row.cleaning_note !== null ? (
      <Text size="xs" c="dimmed" className="leadNote">
        {row.cleaning_note}
      </Text>
    ) : null;
  if (row.rejection_reason !== null) {
    return (
      <Stack gap={4} align="center">
        <Badge variant="light" color="red">
          {leadReasonTitle(row.rejection_reason)}
        </Badge>
        {note}
      </Stack>
    );
  }
  if (note !== null) return note;
  if (row.verification_status !== null) {
    return (
      <Text size="xs" c="dimmed">
        вердикт {row.verification_status}
      </Text>
    );
  }
  return (
    <Text size="sm" c="dimmed">
      —
    </Text>
  );
}

function LeadRow({ row }: { row: LeadCard }) {
  const who = [row.name, row.position].filter((part) => part !== null).join(' · ');
  return (
    <Table.Tr>
      <Table.Td className="cellName">
        {/* Адрес и домен — по швам (`components/Seams`), а не посреди слова: имя то же,
            шов — место переноса без знака. */}
        <Text size="sm" fw={500} className="leadEmail">
          <Seams text={row.email} />
        </Text>
        {who !== '' && (
          <Text size="xs" c="dimmed" className="leadWho">
            {who}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        {row.company !== null && <Text size="sm">{row.company}</Text>}
        <Anchor
          href={`https://${row.host}`}
          target="_blank"
          rel="noreferrer"
          size="xs"
          className="leadHost"
        >
          <Seams text={row.host} />
        </Anchor>
      </Table.Td>
      <Table.Td>
        <Text size="sm">{row.hypothesis}</Text>
      </Table.Td>
      <Table.Td>
        <Text size="sm">{countryTitle(row.country)}</Text>
        {row.timezone !== null && (
          <Text size="xs" c="dimmed">
            {row.timezone}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        <StateBadge state={row.status} />
      </Table.Td>
      <Table.Td>
        <Outcome row={row} />
      </Table.Td>
    </Table.Tr>
  );
}

/** Что предложить у пустой таблицы: сбросить фильтры. Лидов нет вовсе — «Загрузить
 *  базу» уже стоит в строке вкладок, вторая такая же кнопка читалась бы другим действием. */
function EmptyAction({ action, onReset }: { action: Emptiness['action']; onReset: () => void }) {
  if (action !== 'reset') return null;
  return (
    <Button variant="subtle" size="compact-sm" className="press" onClick={onReset}>
      Сбросить фильтры
    </Button>
  );
}

/** Строка во всю ширину: почему пусто и что сделать. */
function Empty({ empty, onReset }: { empty: Emptiness; onReset: () => void }) {
  const { title, detail, action } = empty;
  return (
    <Stack gap={6} align="flex-start" py="sm">
      <Text size="sm" fw={500}>
        {title}
      </Text>
      <Text size="sm" c="dimmed">
        {detail}
      </Text>
      <EmptyAction action={action} onReset={onReset} />
    </Stack>
  );
}

interface Props extends FilterRowProps {
  rows: LeadCard[];
  /** Строки прежнего фильтра — ждут замены. */
  stale: boolean;
  empty: Emptiness | null;
  refusal: string | null;
  onReset: () => void;
}

/** Отказ сервера словами — и до первого ответа, и строкой вместо строк. */
export function LeadsRefused({ refusal }: { refusal: string }) {
  return (
    <Alert color="red" title="Лиды не загрузились">
      {refusal}
    </Alert>
  );
}

/** Что встаёт вместо строк: отказ сервера — или объяснение пустоты. */
function fillerOf(refusal: string | null, empty: Emptiness | null, onReset: () => void) {
  if (refusal !== null) return <LeadsRefused refusal={refusal} />;
  return empty === null ? undefined : <Empty empty={empty} onReset={onReset} />;
}

export function LeadsTable({ rows, stale, empty, refusal, onReset, ...filters }: Props) {
  return (
    <FixedTable
      columns={COLUMNS}
      minWidth={TABLE_MIN_WIDTH}
      className="filteredTable leadsTable"
      head={<FilterRow {...filters} />}
      stale={stale}
      filler={fillerOf(refusal, empty, onReset)}
      rows={rows.map((row) => (
        <LeadRow key={row.id} row={row} />
      ))}
    />
  );
}
