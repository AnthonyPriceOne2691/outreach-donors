/**
 * Таблица доноров с фильтрами в шапке — каждый под своей колонкой.
 *
 * **Фильтр живёт у колонки, которую сужает.** Замечание 25.09.2026: блок
 * фильтров над таблицей убрать, «функционал размазать по фильтрам в
 * таблице». Поиск стоит под «Донором», вердикт — под «Вердиктом», порог DR —
 * под «DR», адрес — под «Адресами»: что сужает колонку, видно, не читая
 * подписей. С 26.09.2026 фильтр есть под каждой колонкой: трафик «не ниже»,
 * страна со счётчиками и поиском по названию, срок метрик.
 *
 * **Отметка — первой колонкой** (замечание 26.09.2026: «заведи возможность
 * отмечать, какие именно доноры можно выгрузить»). Флажок в шапке — все
 * на этой странице; отмечена часть — промежуточное состояние. Имя донора
 * при этом по-прежнему читается слева (`dataTable pickFirst`).
 *
 * **Ширины заданы, раскладка фиксированная.** При автоматической колонки
 * подстраиваются под содержимое страницы, и таблица «прыгает» от страницы
 * к странице и от фильтра к фильтру. Ширины — по самому длинному, что
 * в колонке бывает, включая то, чего нет в сегодняшних данных, и замерены
 * шрифтом экрана: значок, ужатый до многоточия, не значит ничего (урок
 * 21.09.2026), а обрезанное значение фильтра — не то значение.
 *
 * **На телефоне строка фильтров уезжает в прокрутку вместе с таблицей.**
 * Фильтр остаётся под своей колонкой, а не переезжает в отдельную стопку
 * над таблицей: вторая раскладка — второй экран для проверки, и связь
 * «фильтр — колонка» на ней теряется. Поиск по домену и отметка — в первых
 * колонках и видны без прокрутки; к остальным фильтрам ведёт та же
 * прокрутка, что и к их колонкам.
 */

import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
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

import {
  CONTACT_STATUSES,
  countryTitle,
  DONOR_FRESHNESS,
  DONOR_STATUSES,
  NOT_SEARCHED,
} from '../api/labels';
import type { DonorFreshness, DonorRowCard, DonorStatus } from '../api/types';
import { formatCompact, formatNumber, formatShare } from '../format';
import { FRESHNESS, totalOf, TRAFFIC_DIGITS } from './donorFilters';
import type { DonorFilters, Emptiness } from './donorFilters';
import type { Picks } from './picks';

/** Колонки слева направо. Ширина донора — остаток: в ней домен, и ей
 *  отдаётся всё, что не нужно остальным. Остальные — по самому длинному,
 *  что в колонке бывает, замеренному шрифтом экрана (холстом по живому
 *  элементу, 26.09.2026), плюс поля ячейки, поля и стрелки списка:
 *  - вердикт — поле «не проверен · 1 225» (116 px текста);
 *  - DR — подсказка «не ниже» (48);
 *  - трафик — порог «1 000 000 000» (84): миллиарды бывают, у крупнейшего
 *    сайта базы пять;
 *  - гео — строка «Саудовская Аравия · 62%» (173); в поле — только название,
 *    счётчик — в списке: по названию в поле ищут;
 *  - адреса — число и значок «предел запросов» (140);
 *  - данные — поле «не проверялись · 1 225» (135). */
const COLUMNS: { title: string; width?: string }[] = [
  { title: 'Отметка', width: '2.5rem' },
  { title: 'Донор' },
  { title: 'Вердикт', width: '11rem' },
  { title: 'DR', width: '6rem' },
  { title: 'Трафик', width: '8rem' },
  { title: 'Гео', width: '12.25rem' },
  { title: 'Адреса', width: '10rem' },
  { title: 'Данные', width: '12.25rem' },
];

/** Уже этого таблица не сжимается и уезжает в прокрутку: остальным
 *  колонкам — их ширины (992 px), домену — сто шестьдесят, чтобы поместилась
 *  подсказка «Домен или причина». На 1440 px таблица шире (1161) — прокрутки
 *  нет; на 1280 и уже — есть. */
export const TABLE_MIN_WIDTH = 1152;

/** Стрелка списка в поле фильтра — уже умолчания (28 px): на 1440 px шесть
 *  пикселей на поле — это место, которого не хватало колонке домена. */
const ARROW_WIDTH = 22;

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

interface FilterRowProps {
  filters: DonorFilters;
  facets: Facets | null;
  /** Поиск и пороги — как набраны сейчас: в адрес они уходят после паузы. */
  search: string;
  onSearch: (value: string) => void;
  minDr: number | null;
  onMinDr: (value: number | null) => void;
  minTraffic: number | null;
  onMinTraffic: (value: number | null) => void;
  onFilter: (patch: Partial<DonorFilters>) => void;
}

function numberOf(value: number | string): number | null {
  return typeof value === 'number' ? value : null;
}

function FilterRow({
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
            { value: 'all', label: counted('все', all) },
            ...STATUSES.map((status) => ({
              value: status,
              label: counted(
                DONOR_STATUSES[status].title,
                facets === null ? undefined : (facets.counts[status] ?? 0),
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
          onChange={(value) => onMinDr(numberOf(value))}
        />
      </Table.Th>
      <Table.Th>
        {/* Разряды — пробелом, как у чисел на экране: «1 000 000» читается,
            «1000000» — пересчитывается по нулям. */}
        <NumberInput
          size="xs"
          placeholder="не ниже"
          aria-label="Трафик не ниже"
          min={0}
          max={10 ** TRAFFIC_DIGITS - 1}
          clampBehavior="strict"
          allowDecimal={false}
          allowNegative={false}
          thousandSeparator=" "
          hideControls
          value={minTraffic ?? ''}
          onChange={(value) => onMinTraffic(numberOf(value))}
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
            { value: 'all', label: counted('все', all) },
            ...FRESHNESS.map((state) => ({
              value: state,
              label: counted(
                DONOR_FRESHNESS[state].title,
                facets === null ? undefined : (facets.freshness[state] ?? 0),
              ),
            })),
          ]}
        />
      </Table.Th>
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

interface RowProps {
  donor: DonorRowCard;
  from: string;
  picked: boolean;
  onPick: (id: number) => void;
  onOpen: (donor: DonorRowCard) => void;
}

function DonorRow({ donor, from, picked, onPick, onOpen }: RowProps) {
  // Слово считает сервер тем же условием, что фильтр «Данные»: данных
  // не было — «не проверялись», а не «пора обновить» — обновлять нечего.
  const fresh = DONOR_FRESHNESS[donor.freshness];
  return (
    <Table.Tr style={{ cursor: 'pointer' }} onClick={() => onOpen(donor)}>
      {/* Щелчок по ячейке отметки не открывает карточку: промахнуться мимо
          флажка на пару пикселей — не повод уходить со страницы. */}
      <Table.Td onClick={(event) => event.stopPropagation()}>
        <Checkbox
          size="sm"
          aria-label={`Отметить ${donor.host}`}
          checked={picked}
          onChange={() => onPick(donor.id)}
        />
      </Table.Td>
      <Table.Td>
        {/* Ссылка, а не только строка: с клавиатуры до строки не дойти,
            а карточку донора открывают и в новой вкладке. Цвет — чернила,
            а не акцент: щёлкают строку целиком, её подсветка и есть отклик,
            а бирюзовая ссылка в верхних строках стоит на бирюзовом углу
            полотна — в тёмной теме это 4,3–4,9 : 1 в зависимости от того,
            где сейчас плывёт пятно (замер 25.09.2026). */}
        <Anchor
          component={Link}
          to={{ pathname: `/donors/${donor.id}` }}
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

function EmptyRow({ empty, onReset }: { empty: Emptiness; onReset: () => void }) {
  return (
    <WholeRow>
      <Stack gap={6} align="flex-start" py="sm">
        <Text size="sm" fw={500}>
          {empty.title}
        </Text>
        <Text size="sm" c="dimmed" maw={720}>
          {empty.detail}
        </Text>
        <Group gap="sm">
          {empty.path !== undefined && (
            <Button component={Link} to={empty.path.to} size="compact-sm" className="press">
              {empty.path.label}
            </Button>
          )}
          {empty.resettable && (
            <Button variant="subtle" size="compact-sm" className="press" onClick={onReset}>
              Сбросить фильтры
            </Button>
          )}
        </Group>
      </Stack>
    </WholeRow>
  );
}

/** Флажок «все на этой странице»: отмечена часть — промежуточное состояние. */
function PickAll({ rows, picks }: { rows: DonorRowCard[]; picks: Picks }) {
  const ids = rows.map((donor) => donor.id);
  const on = ids.filter((id) => picks.picked.has(id)).length;
  const all = ids.length > 0 && on === ids.length;
  return (
    <Checkbox
      size="sm"
      aria-label="Отметить всех на этой странице"
      checked={all}
      indeterminate={on > 0 && !all}
      disabled={ids.length === 0}
      onChange={() => picks.setAll(ids, !all)}
    />
  );
}

interface Props extends FilterRowProps {
  rows: DonorRowCard[];
  /** Идёт запрос новой страницы — строки старые, и это видно. */
  stale: boolean;
  empty: Emptiness | null;
  refusal: string | null;
  /** Откуда пришли в карточку: адрес списка с фильтрами — для «назад». */
  from: string;
  picks: Picks;
  onOpen: (donor: DonorRowCard) => void;
  onReset: () => void;
}

export function DonorsTable({
  rows,
  stale,
  empty,
  refusal,
  from,
  picks,
  onOpen,
  onReset,
  ...filters
}: Props) {
  return (
    <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
      <Table
        className="dataTable pickFirst filteredTable donorsTable"
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
            <Table.Th>
              <PickAll rows={stale ? [] : rows} picks={picks} />
            </Table.Th>
            {COLUMNS.slice(1).map((column) => (
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
        <Table.Tbody className="staleRows" data-stale={stale || undefined}>
          {refusal !== null && (
            <WholeRow>
              <Alert color="red" title="Доноры не загрузились">
                {refusal}
              </Alert>
            </WholeRow>
          )}
          {refusal === null && empty !== null && <EmptyRow empty={empty} onReset={onReset} />}
          {refusal === null &&
            rows.map((donor) => (
              <DonorRow
                key={donor.id}
                donor={donor}
                from={from}
                picked={picks.picked.has(donor.id)}
                onPick={picks.toggle}
                onOpen={onOpen}
              />
            ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
