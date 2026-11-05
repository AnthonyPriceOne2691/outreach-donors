/**
 * Таблица очереди проверки — спорные или «куплено»: кому ссылается донор, балл и за
 * что он, найденная ссылка, а у кого есть право — «Пишем» и «Не пишем».
 *
 * Своим файлом, как таблицы списка (`AdvertiserListTable`) и обхода (`CrawlTable`):
 * на экране рекламодателей три вида, и очередь — часть одного из них. Зачем проверка
 * и как устроен её вид — в `AdvertisersPage.tsx`.
 *
 * **Фильтры — под заголовками своих колонок** (слово Anthony 05.11.2026): «Балл» — точным значением,
 * «За что» — признаком скоринга, теми же словами, что у разбивки спорных (`DisputedStrata`). Варианты
 * и числа — от сервера, тем же ответом, что строки.
 */

import { Anchor, Badge, Button, Group, Stack, Table, Text } from '@mantine/core';

import type { JudgeAdvice, QueueFacet, QueueFacets, ReviewRow } from '../api/advertisers';
import type { CandidateCard } from '../api/types';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { FilterSelect } from '../components/FilterSelect';
import { Seams } from '../components/Seams';
import { NoRows } from '../components/WholeRow';
import type { Emptiness } from '../components/WholeRow';
import { formatDate, formatNumber, plural } from '../format';
import { LinkAnchor } from './LinkAnchor';
import type { ReviewFilters } from './reviewFilters';

/** Как называется каждый исход скоринга на человеческом языке. */
export const VERDICTS: Record<CandidateCard['verdict'], { label: string; color: string }> = {
  bought: { label: 'куплена', color: 'green' },
  pending: { label: 'спорно', color: 'yellow' },
  skipped: { label: 'мимо', color: 'gray' },
  blocked: { label: 'кому не пишем', color: 'gray' },
};

/** Колонки слева направо. **Остаток — «За что»**: в ней причины скоринга, по строке
 *  на причину, и в 15rem они переносились, а «+2» вставало на свою строку — строка
 *  таблицы росла до 130–160 px, пока «Кому ссылается» держала ~300 px под короткий
 *  домен (аудит экранов 09.10.2026). Домену — 13rem, он переносится по швам; «Ссылка» —
 *  текст; остальные — по самому длинному шрифтом экрана (25.09.2026): кнопки «Пишем» и
 *  «Не пишем» с зазором — около 154 px. Плюс 32 px полей ячейки и запас. «Балл» — по полю
 *  фильтра со словом «все» (замер 05.11.2026: 21 px текста, 34 px полей и стрелки). */
const COLUMNS: Column[] = [
  { title: 'Кому ссылается', width: '13rem' },
  { title: 'Донор', width: '11rem' },
  { title: 'Балл', width: '5.5rem' },
  { title: 'За что' },
  { title: 'Ссылка', width: '11.75rem' },
];
const ACTIONS_WIDTH = '12.5rem';
/** Уже — прокрутка: колонкам с шириной 53,75rem (860 px), «За что» — от поля фильтра с полями
 *  ячейки. На 1440 с открытым меню таблице есть 1 101 px, и прокрутки нет. */
const TABLE_MIN_WIDTH = 1100;
/** Поле «За что» — по самому длинному признаку (замер 05.11.2026: 172 px текста, 34 px полей и
 *  стрелки), а не во всю широкую колонку: посередине неё. */
const FEATURE_FIELD = '13rem';

/** «Пишем» и «Не пишем» — у кого есть право решать. */
function Decide({
  row,
  busy,
  stale,
  onDecide,
}: {
  row: CandidateCard;
  /** «Пишем» этой строки уходит на сервер. */
  busy: boolean;
  /** Строка прежнего запроса: решать по ней нельзя — она не того списка. */
  stale: boolean;
  onDecide: (row: CandidateCard, confirmed: boolean) => void;
}) {
  return (
    <Table.Td className="cellDecide">
      {/* Обе кнопки — одного веса: экран меряет ошибки скоринга, и залитая «Пишем»
          в каждой строке подталкивала соглашаться с ним (аудит экранов 09.10.2026). */}
      <Group gap="xs" justify="center" wrap="nowrap">
        <Button
          size="compact-sm"
          variant="light"
          className="press"
          loading={busy}
          disabled={stale}
          onClick={() => onDecide(row, true)}
        >
          Пишем
        </Button>
        <Button
          size="compact-sm"
          variant="default"
          className="press"
          disabled={stale}
          onClick={() => onDecide(row, false)}
        >
          Не пишем
        </Button>
      </Group>
    </Table.Td>
  );
}

/** Совет судьи словами. */
const ADVICE: Record<JudgeAdvice['advice'], string> = {
  write: 'пишем',
  skip: 'не пишем',
  unclear: 'не понять',
};

/** Совет судьи под решением человека — для сверки глазами: что сказал, каким правилом и по какой
 *  цитате. Сервер даёт его только решённой строке. */
function JudgeLine({ said }: { said: JudgeAdvice }) {
  const quote = said.link_quote || said.target_quote;
  return (
    <Text size="xs" c="dimmed" className="cellName">
      судья: {ADVICE[said.advice]} — {said.words}
      {quote ? ` · «${quote}»` : ''}
    </Text>
  );
}

/** Ячейки строки до кнопок решения. */
function Cells({ row }: { row: ReviewRow }) {
  return (
    <>
      <Table.Td>
        {/* Имя — чернилами, как во всех таблицах: бирюзовая ссылка на бирюзовом
            углу полотна не держит норму. */}
        <Anchor
          href={`https://${row.target_root}`}
          target="_blank"
          rel="noreferrer"
          c="var(--ink)"
          fw={500}
          underline="hover"
          className="cellName"
        >
          <Seams text={row.target_root} />
        </Anchor>
        {row.confirmed !== null ? (
          <Text size="xs" c="dimmed" className="cellName">
            {row.confirmed ? 'пишем' : 'не пишем'} — {row.decided_by}
          </Text>
        ) : null}
        {row.confirmed !== null && row.judge ? <JudgeLine said={row.judge} /> : null}
      </Table.Td>
      <Table.Td className="wrapCell cellName" data-label="Донор">
        <Seams text={row.donor_host} />
      </Table.Td>
      <Table.Td data-label="Балл">
        <Badge variant="light" color={VERDICTS[row.verdict].color}>
          {row.points}
        </Badge>
      </Table.Td>
      {/* Причины — строками по центру, как всё в колонке, без маркеров списка:
          маркеры стояли у левого края, а текст посередине, и строки читались
          оторванными от них. */}
      <Table.Td className="wrapCell" data-label="За что">
        <Stack gap={2}>
          {row.reasons.map((reason) => (
            <Text key={reason} size="xs">
              {reason}
            </Text>
          ))}
          <Text size="xs" c="dimmed">
            {formatNumber(row.links)} {plural(row.links, 'ссылка', 'ссылки', 'ссылок')} с{' '}
            {formatNumber(row.pages)} {plural(row.pages, 'страницы', 'страниц', 'страниц')}
          </Text>
        </Stack>
      </Table.Td>
      <Table.Td className="wrapCell" data-label="Ссылка">
        {row.best_page_url ? (
          <Anchor href={row.best_page_url} target="_blank" rel="noreferrer" size="sm">
            страница
          </Anchor>
        ) : (
          '—'
        )}
        {row.best_anchor ? <LinkAnchor anchor={row.best_anchor} /> : null}
        {row.best_published ? (
          <Text size="xs" c="dimmed">
            статья от {formatDate(row.best_published)}
          </Text>
        ) : null}
      </Table.Td>
    </>
  );
}

/** Поле фильтра: варианты и числа — от сервера. Значение из адреса, которого в списке нет, — всё
 *  равно вариант: иначе поле показало бы «все», а строки были бы сужены до него. */
function FacetFilter({
  label,
  facet,
  value,
  width,
  onPick,
}: {
  label: string;
  facet: QueueFacet;
  value: string | null;
  width?: string;
  onPick: (value: string | null) => void;
}) {
  const choiceOf = (key: string) => facet.choices.find((choice) => choice.key === key);
  const keys = facet.choices.map((choice) => choice.key);
  if (value !== null && !keys.includes(value)) keys.push(value);
  return (
    <FilterSelect
      label={label}
      value={value}
      keys={keys}
      title={(key) => choiceOf(key)?.title ?? key}
      count={(key) => (key === 'all' ? facet.total : (choiceOf(key)?.count ?? 0))}
      onPick={onPick}
      contentList
      {...(width !== undefined ? { width } : {})}
    />
  );
}

interface FilterProps {
  filters: ReviewFilters;
  facets: QueueFacets;
  onFilter: (patch: Partial<ReviewFilters>) => void;
}

/** Фильтры под заголовками: «Балл» и «За что»; у домена, донора и ссылки своего поля нет. */
function FilterRow({ filters, facets, onFilter, tail }: FilterProps & { tail: boolean }) {
  return (
    <Table.Tr className="filterRow">
      <Table.Th />
      <Table.Th />
      <Table.Th data-label="Балл">
        <FacetFilter
          label="Балл"
          facet={facets.points}
          value={filters.points}
          onPick={(points) => onFilter({ points })}
        />
      </Table.Th>
      <Table.Th data-label="За что">
        <Group justify="center">
          <FacetFilter
            label="За что"
            facet={facets.features}
            value={filters.feature}
            width={FEATURE_FIELD}
            onPick={(feature) => onFilter({ feature })}
          />
        </Group>
      </Table.Th>
      <Table.Th />
      {tail ? <Table.Th /> : null}
    </Table.Tr>
  );
}

interface Props extends FilterProps {
  rows: ReviewRow[];
  /** Едет новая страница — прежние строки приглушены, решать по ним нельзя. */
  stale: boolean;
  /** Почему строк нет — под фильтром пусто; строки есть или едут — `null`. */
  empty: Emptiness | null;
  onReset: () => void;
  /** Право решать: без него нет ни кнопок, ни их колонки. */
  mayDecide: boolean;
  /** Строка, чьё «Пишем» сейчас уходит на сервер. */
  deciding: number | null;
  onDecide: (row: CandidateCard, confirmed: boolean) => void;
}

export function CandidatesTable({
  rows,
  stale,
  empty,
  onReset,
  mayDecide,
  deciding,
  onDecide,
  ...filters
}: Props) {
  return (
    <DataTable
      columns={COLUMNS}
      minWidth={TABLE_MIN_WIDTH}
      className="filteredTable"
      tail={mayDecide ? ACTIONS_WIDTH : null}
      head={<FilterRow {...filters} tail={mayDecide} />}
      stale={stale}
    >
      <NoRows
        span={COLUMNS.length + (mayDecide ? 1 : 0)}
        refusal={null}
        failTitle="Очередь не загрузилась"
        empty={empty}
        onReset={onReset}
      />
      {rows.map((row) => (
        <Table.Tr key={row.id}>
          <Cells row={row} />
          {mayDecide ? (
            <Decide row={row} busy={deciding === row.id} stale={stale} onDecide={onDecide} />
          ) : null}
        </Table.Tr>
      ))}
    </DataTable>
  );
}
