/**
 * Таблица доноров с фильтрами в шапке — каждый под своей колонкой.
 *
 * **Фильтр живёт у колонки, которую сужает.** Замечание 25.09.2026: блок
 * фильтров над таблицей убрать, «функционал размазать по фильтрам в
 * таблице». Поиск стоит под «Донором», вердикт — под «Вердиктом», порог DR —
 * под «DR», адрес — под «Адресами»: что сужает колонку, видно, не читая
 * подписей.
 *
 * **Ширины заданы, раскладка фиксированная.** При автоматической колонки
 * подстраиваются под содержимое страницы, и таблица «прыгает» от страницы
 * к странице и от фильтра к фильтру. Ширины подобраны так, чтобы самый
 * длинный значок влезал целиком: значок, ужатый до многоточия, не значит
 * ничего (урок 21.09.2026).
 *
 * **На телефоне строка фильтров уезжает в прокрутку вместе с таблицей.**
 * Фильтр остаётся под своей колонкой, а не переезжает в отдельную стопку
 * над таблицей: вторая раскладка — второй экран для проверки, и связь
 * «фильтр — колонка» на ней теряется. Поиск по домену — в первой колонке
 * и виден без прокрутки; к остальным фильтрам ведёт та же прокрутка, что
 * и к их колонкам.
 */

import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  NumberInput,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { CONTACT_STATUSES, countryTitle, DONOR_STATUSES, NOT_SEARCHED } from '../api/labels';
import type { DonorRowCard, DonorStatus } from '../api/types';
import { formatCompact, formatNumber, formatShare } from '../format';
import { totalOf } from './donorFilters';
import type { DonorFilters } from './donorFilters';

/** Колонки слева направо. Ширина первой — остаток: в ней домен, и ей
 *  отдаётся всё, что не нужно остальным. Остальные — по самому длинному
 *  содержимому, замеренному шрифтом экрана (25.09.2026): поле «не подходит
 *  · 1 225» — 159 px, число со значком «предел запросов» — 140, значок «не
 *  проверялись» — 118, подсказка «не ниже» — 71, плюс поля ячейки. Страна
 *  с длинным именем («Великобритания · 62%») переносится на вторую строку,
 *  а не расширяет колонку: домену место нужнее. */
const COLUMNS: { title: string; width?: string }[] = [
  { title: 'Донор' },
  { title: 'Вердикт', width: '11.25rem' },
  { title: 'DR', width: '6rem' },
  { title: 'Трафик', width: '5.75rem' },
  { title: 'Гео', width: '9.5rem' },
  { title: 'Адреса', width: '10.25rem' },
  { title: 'Данные', width: '8.75rem' },
];

/** Уже этого таблица не сжимается и уезжает в прокрутку: остальным
 *  колонкам — их ширины (824 px), домену — не меньше ста семидесяти. */
export const TABLE_MIN_WIDTH = 1000;

const STATUSES = Object.keys(DONOR_STATUSES) as DonorStatus[];

const ADDRESS_OPTIONS = [
  { value: 'any', label: 'все' },
  // Слово то же, что у плитки «С адресом» на главной, и правило то же:
  // исход поиска «адрес найден». «Без адреса» — и «не нашли», и «не искали».
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

interface FilterRowProps {
  filters: DonorFilters;
  /** Сводка по всей базе. `null` — ещё не пришла: без чисел, а не с нулями. */
  counts: Record<string, number> | null;
  /** Поиск и порог DR — как набраны сейчас: в адрес они уходят после паузы. */
  search: string;
  onSearch: (value: string) => void;
  minDr: number | null;
  onMinDr: (value: number | null) => void;
  onFilter: (patch: Partial<DonorFilters>) => void;
}

function FilterRow({
  filters,
  counts,
  search,
  onSearch,
  minDr,
  onMinDr,
  onFilter,
}: FilterRowProps) {
  const all = counts === null ? undefined : totalOf(counts);
  return (
    <Table.Tr className="donorsFilters">
      <Table.Th>
        {/* Без значка лупы: на ноутбуке в 1280 px колонка домена — 177 px,
            и значок съедал ровно те пять пикселей, которых не хватало
            подсказке. Что это поиск, говорит сама подсказка. */}
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
          allowDeselect={false}
          value={filters.status ?? 'all'}
          onChange={(value) =>
            onFilter({ status: STATUSES.find((status) => status === value) ?? null })
          }
          data={[
            { value: 'all', label: counted('все', all) },
            ...STATUSES.map((status) => ({
              value: status,
              label: counted(
                DONOR_STATUSES[status].title,
                counts === null ? undefined : (counts[status] ?? 0),
              ),
            })),
          ]}
        />
      </Table.Th>
      <Table.Th>
        <NumberInput
          size="xs"
          placeholder="не ниже"
          aria-label="DR не ниже"
          min={0}
          max={100}
          clampBehavior="strict"
          allowDecimal={false}
          allowNegative={false}
          hideControls
          value={minDr ?? ''}
          onChange={(value) => onMinDr(typeof value === 'number' ? value : null)}
        />
      </Table.Th>
      <Table.Th />
      <Table.Th />
      <Table.Th>
        <Select
          size="xs"
          aria-label="Адреса"
          allowDeselect={false}
          value={addressValue(filters.address)}
          onChange={(value) =>
            onFilter({ address: value === 'yes' ? true : value === 'no' ? false : null })
          }
          data={ADDRESS_OPTIONS}
        />
      </Table.Th>
      <Table.Th />
    </Table.Tr>
  );
}

/** «Не проверен» — не «не подходит». Пояснение не теряется вместе с блоком,
 *  где оно стояло: оно у заголовка колонки, прямо над фильтром вердикта. */
function VerdictHint() {
  return (
    <Tooltip
      multiline
      w={280}
      withArrow
      events={{ hover: true, focus: true, touch: true }}
      label="«Не проверен» — не «не подходит»: у домена не было данных, и его надо добрать позже, а не закрыть. Причина отсева — под доменом."
    >
      <ActionIcon
        variant="subtle"
        size="sm"
        radius="xl"
        color="gray"
        aria-label="Что значит «не проверен»"
      >
        <IconInfoCircle size={15} />
      </ActionIcon>
    </Tooltip>
  );
}

function AddressCell({ donor }: { donor: DonorRowCard }) {
  const state =
    donor.contact_status === null ? NOT_SEARCHED : CONTACT_STATUSES[donor.contact_status];
  return (
    // Число и значок — одной группой постоянной ширины: числа встают
    // столбиком, значки начинаются с одной линии, а группа — по центру.
    <span className="addressCell">
      <span className="addressCount">{formatNumber(donor.contacts)}</span>
      <Badge variant="light" size="sm" color={state.color}>
        {state.title}
      </Badge>
    </span>
  );
}

function freshness(donor: DonorRowCard): { title: string; color: string } {
  // Слово то же, что в карточке: данных не было — «не проверялись»,
  // а не «пора обновить» — обновлять нечего.
  if (donor.metrics_refreshed_at === null) return { title: 'не проверялись', color: 'gray' };
  return donor.fresh
    ? { title: 'в сроке', color: 'green' }
    : { title: 'пора обновить', color: 'gray' };
}

interface RowProps {
  donor: DonorRowCard;
  href: { pathname: string };
  from: string;
  onOpen: (donor: DonorRowCard) => void;
}

function DonorRow({ donor, href, from, onOpen }: RowProps) {
  const fresh = freshness(donor);
  return (
    <Table.Tr style={{ cursor: 'pointer' }} onClick={() => onOpen(donor)}>
      <Table.Td>
        {/* Ссылка, а не только строка: с клавиатуры до строки не дойти,
            а карточку донора открывают и в новой вкладке. Цвет — чернила,
            а не акцент: щёлкают строку целиком, её подсветка и есть отклик,
            а бирюзовая ссылка в верхних строках стоит на бирюзовом углу
            полотна — в тёмной теме это 4,3–4,9 : 1 в зависимости от того,
            где сейчас плывёт пятно (замер 25.09.2026). */}
        <Anchor
          component={Link}
          to={href}
          state={{ from }}
          fw={500}
          c="var(--ink)"
          underline="hover"
          className="donorHost"
          onClick={(event) => event.stopPropagation()}
        >
          {donor.host}
        </Anchor>
        {donor.reject_reason !== null && (
          <Text size="xs" c="dimmed">
            {donor.reject_reason}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        <Group justify="center">
          <Badge variant="light" color={DONOR_STATUSES[donor.status].color}>
            {DONOR_STATUSES[donor.status].title}
          </Badge>
        </Group>
      </Table.Td>
      <Table.Td>{formatNumber(donor.dr)}</Table.Td>
      <Table.Td>{formatCompact(donor.org_traffic)}</Table.Td>
      <Table.Td>
        {donor.geo === null
          ? '—'
          : `${countryTitle(donor.geo)}${
              donor.geo_top_share === null ? '' : ` · ${formatShare(donor.geo_top_share)}`
            }`}
      </Table.Td>
      <Table.Td>
        <AddressCell donor={donor} />
      </Table.Td>
      <Table.Td>
        {/* Свежесть — это про деньги: за свежие данные второй раз
            не платят, поэтому она видна в таблице, а не в карточке. */}
        <Group justify="center">
          <Badge variant="light" color={fresh.color}>
            {fresh.title}
          </Badge>
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}

/** Одна строка на всю ширину: пустой результат, отказ сервера. Фильтры над
 *  ней остаются — поправить условие можно тут же, не возвращаясь. */
function WholeRow({ children }: { children: ReactNode }) {
  return (
    <Table.Tr className="wholeRow">
      <Table.Td colSpan={COLUMNS.length}>{children}</Table.Td>
    </Table.Tr>
  );
}

export interface Emptiness {
  title: string;
  detail: string;
  /** Есть ли что сбросить: без фильтров кнопка ничего бы не сделала. */
  resettable: boolean;
}

interface Props extends FilterRowProps {
  rows: DonorRowCard[];
  /** Идёт запрос новой страницы — строки старые, и это видно. */
  stale: boolean;
  empty: Emptiness | null;
  refusal: string | null;
  /** Откуда пришли в карточку: адрес списка с фильтрами — для «назад». */
  from: string;
  onOpen: (donor: DonorRowCard) => void;
  onReset: () => void;
}

export function DonorsTable({
  rows,
  stale,
  empty,
  refusal,
  from,
  onOpen,
  onReset,
  ...filters
}: Props) {
  return (
    <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
      <Table
        className="dataTable donorsTable"
        layout="fixed"
        tabularNums
        verticalSpacing="sm"
        horizontalSpacing="xs"
      >
        <colgroup>
          {COLUMNS.map((column) => (
            <col key={column.title} style={column.width ? { width: column.width } : undefined} />
          ))}
        </colgroup>
        <Table.Thead>
          <Table.Tr>
            {COLUMNS.map((column) => (
              <Table.Th key={column.title}>
                {column.title === 'Вердикт' ? (
                  <Group gap={2} justify="center" wrap="nowrap">
                    {column.title}
                    <VerdictHint />
                  </Group>
                ) : (
                  column.title
                )}
              </Table.Th>
            ))}
          </Table.Tr>
          <FilterRow {...filters} />
        </Table.Thead>
        <Table.Tbody data-stale={stale || undefined}>
          {refusal !== null && (
            <WholeRow>
              <Alert color="red" title="Доноры не загрузились">
                {refusal}
              </Alert>
            </WholeRow>
          )}
          {refusal === null && empty !== null && (
            <WholeRow>
              <Stack gap={6} align="flex-start" py="sm">
                <Text size="sm" fw={500}>
                  {empty.title}
                </Text>
                <Text size="sm" c="dimmed">
                  {empty.detail}
                </Text>
                {empty.resettable && (
                  <Button variant="subtle" size="compact-sm" className="press" onClick={onReset}>
                    Сбросить фильтры
                  </Button>
                )}
              </Stack>
            </WholeRow>
          )}
          {refusal === null &&
            rows.map((donor) => (
              <DonorRow
                key={donor.id}
                donor={donor}
                href={{ pathname: `/donors/${donor.id}` }}
                from={from}
                onOpen={onOpen}
              />
            ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
