/**
 * Таблица сайтов прогона: сайт и ключ, полоса (и «было» / «стало бы» после пересуда), что сказал
 * судья, кто решил и когда, почему и цитата, итог — и решение человека на вкладке «На проверку».
 *
 * **Колонки — по вкладке**: колонки, которой у вкладки нечего показать, нет (`docs/UI_RULES.md`,
 * «Таблицы»): итог — только у вкладок, где бывают принятые, решение — только «на проверку», судья
 * и «кто решил» — не у «ещё не судили». Фильтр «кто решил» — под своей колонкой.
 *
 * **Длинное не ломает строку**: домен — по швам (`Seams`), причина и цитата — две строки,
 * целиком — в подсказке; на телефоне строка — карточкой (`DataTable`, `phoneCards`): сайт первым,
 * решение — сразу под ним, остальное — с подписью колонки, и длинное — целиком: наведения на
 * телефоне нет, подсказка там не открылась бы (`docs/UI_RULES.md`, «Длинный текст в ячейке не
 * живёт»), а у причины в карточке вся ширина.
 *
 * **Решённая строка стоит на месте** до смены вкладки или страницы — с пометкой: «принят —
 * записываем…», пока сервер не ответил, потом «решено: принят»; строки снизу не поднимаются под
 * курсор, и второе нажатие не уходит в соседний сайт. Соседние строки решаются, не дожидаясь ответа.
 *
 * **На «На проверку» причина — целиком**: по ней решают (правило «Длинный текст в ячейке не
 * живёт» — кроме того, по чему решают сейчас); на остальных вкладках — две строки, целиком — в
 * подсказке. Причина переносится в своей колонке, кнопки — в своей (находка стенда 14.10.2026).
 */

import { Anchor, Badge, Button, Group, Select, Stack, Table, Text, Tooltip } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { Link } from 'react-router-dom';

import { LEAD_STATES, leadReasonTitle } from '../api/salesLabels';
import type { LeadState } from '../api/salesTypes';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { Seams } from '../components/Seams';
import { formatDateTime, formatNumber } from '../format';
import { dropdownBelow } from '../theme';
import type { SegmentLane } from './segmentApi';
import { VERDICT_WORDS } from './segmentData';
import type { SegmentSite, SegmentSitesPage, SiteDecider, SiteTab } from './SegmentSitesApi';
import {
  DECIDER_KEYS,
  DECIDER_WORDS,
  LANE_COLORS,
  LANE_WORDS,
  personOf,
} from './SegmentSitesWords';

// Ширины — чтобы таблица вставала в панель на 1440 при раскрытом меню (замер стенда: около
// 1 040 px под таблицу): «Итог» уезжал в прокрутку.
const SITE: Column = { title: 'Сайт', width: '14rem' };
const LANE: Column = { title: 'Полоса', width: '8.5rem' };
/** Полоса там, где бывает «ещё не судили», — по этому самому длинному бейджу: в 8.5rem он
 *  обрезался многоточием (замер стенда). Во «На проверку» его не бывает, а лишний rem увёл бы
 *  её таблицу на 1440 в прокрутку. */
const LANE_WIDE: Column = { title: LANE.title, width: '9.5rem' };
const JUDGE: Column = { title: 'Судья', width: '7.5rem' };
const WHO: Column = { title: 'Кто решил', width: '9rem' };
/** Причина и цитата — остаток ширины: их длина ничем не ограничена. */
const WHY: Column = { title: 'Почему' };
const OUTCOME: Column = { title: 'Итог', width: '10rem' };
/** «Принять» и «Отказ» в ряд с зазором и полями ячейки. */
const DECIDE: Column = { title: 'Решение', width: '11.5rem' };

/** На телефоне строка — карточкой с короткими значениями в ряд (`phoneCardsRow`): полоса, судья
 *  и кто решил — рядом, причина и итог — во всю ширину карточки. В таблице сетки нет, и правило
 *  ни на что не действует. */
const WHOLE_LINE = { gridColumn: '1 / -1' } as const;
/** Ряд коротких значений карточки: полоса — шириной самого длинного бейджа («ещё не судили»),
 *  но не уже своего бейджа (он не переносится), строка «стало бы: …» под ним — переносом; судья
 *  и кто решил — поровну остатком; у всех карточек колонки одни. Поровну на троих полоса не
 *  влезала: «на проверке» и «ещё не судили» обрезались многоточием (замер стенда на 390:
 *  колонка 86 px, слову нужно 75–88 px и поля бейджа). */
const CARD_ROW = { gridTemplateColumns: 'minmax(min-content, 7rem) 1fr 1fr' } as const;

/** Строка — карточкой: тот же порог, что у `phoneCards` (`styles/glass.css`). */
const CARDS = '(max-width: 47.99em)';
/** Длинное в ячейке — две строки, целиком — в подсказке; в карточке — целиком. */
const CLAMP = 2;

/** Место под причину на узкой раскладке: две строки по 28–30 знаков. */
const WHY_MIN_PX = 232;
const REM_PX = 16;

/** Решение человека этого вида по строке «на проверку»: куда и записано ли сервером. */
export interface Held {
  site: SegmentSite;
  lane: 'accept' | 'reject';
  written: boolean;
}

/** Колонки вкладки — без тех, которым на ней нечего показать. */
export function columnsOf(tab: SiteTab): Column[] {
  const shown = [SITE, tab === 'all' || tab === 'pending' ? LANE_WIDE : LANE];
  if (tab !== 'pending') shown.push(JUDGE, WHO);
  shown.push(WHY);
  if (tab === 'all' || tab === 'accept' || tab === 'changed') shown.push(OUTCOME);
  if (tab === 'review') shown.push(DECIDE);
  return shown;
}

/** Уже этого таблица уезжает в прокрутку: сумма ширин и место причине. */
export function minWidthOf(columns: Column[]): number {
  return columns.reduce(
    (sum, column) =>
      sum + (column.width === undefined ? WHY_MIN_PX : parseFloat(column.width) * REM_PX),
    0,
  );
}

/** Сколько строк длинному тексту: в таблице — две, в карточке — сколько нужно. */
type Clamp = number | undefined;

/** Обрезка длинного свойством Mantine: в таблице — `lineClamp`, в карточке — никакой. */
function clamped(clamp: Clamp): { lineClamp?: number } {
  return clamp === undefined ? {} : { lineClamp: clamp };
}

function SiteCell({ site, clamp }: { site: SegmentSite; clamp: Clamp }) {
  return (
    <Table.Td className="cellName">
      <Stack gap={0}>
        {site.page_url !== null ? (
          <Anchor
            href={site.page_url}
            target="_blank"
            rel="noreferrer"
            size="sm"
            c="var(--ink)"
            underline="always"
            className="inkLink"
            {...clamped(clamp)}
            title={site.host}
          >
            <Seams text={site.host} />
          </Anchor>
        ) : (
          <Text size="sm" {...clamped(clamp)} title={site.host}>
            <Seams text={site.host} />
          </Text>
        )}
        {site.keyword !== null && (
          <Text size="xs" c="dimmed" truncate={clamp !== undefined} title={site.keyword}>
            по ключу «{site.keyword}»
          </Text>
        )}
      </Stack>
    </Table.Td>
  );
}

function LaneCell({ site, decided }: { site: SegmentSite; decided: SegmentLane | undefined }) {
  const lane = decided ?? site.lane;
  const change = site.change;
  return (
    <Table.Td data-label={LANE.title} className="wrapCell">
      <Stack gap={2} align="center">
        <Badge variant="light" color={LANE_COLORS[lane]}>
          {LANE_WORDS[lane]}
        </Badge>
        {change !== null && (
          <Text size="xs" c="dimmed" fw={change.would ? 600 : undefined}>
            {change.would
              ? `стало бы: ${LANE_WORDS[change.now]}`
              : `было: ${LANE_WORDS[change.was]}`}
          </Text>
        )}
      </Stack>
    </Table.Td>
  );
}

function verdictOf(site: SegmentSite): string {
  if (site.unanswered) return 'не ответил';
  return site.verdict === null ? 'не судил' : (VERDICT_WORDS[site.verdict] ?? site.verdict);
}

function JudgeCell({ site }: { site: SegmentSite }) {
  // Вердикта нет — бледными чернилами: «не судил» и «не ответил» — не мнение судьи.
  const quiet = site.verdict === null || site.unanswered;
  return (
    <Table.Td data-label={JUDGE.title}>
      <Text size="sm" {...(quiet ? { c: 'dimmed' } : {})}>
        {verdictOf(site)}
      </Text>
    </Table.Td>
  );
}

function WhoCell({ site, decided }: { site: SegmentSite; decided: boolean }) {
  const who = decided ? 'human' : site.decided_by;
  const author = site.decided_by_user;
  return (
    <Table.Td data-label={WHO.title}>
      {who === null ? (
        <Text size="sm" c="dimmed">
          никто
        </Text>
      ) : (
        <Stack gap={0} align="center">
          <Text size="sm" title={author ?? undefined}>
            {DECIDER_WORDS[who]}
            {who === 'human' && author !== null ? ` · ${personOf(author)}` : ''}
          </Text>
          {site.decided_at !== null && !decided && (
            <Text size="xs" c="dimmed">
              {formatDateTime(site.decided_at)}
            </Text>
          )}
        </Stack>
      )}
    </Table.Td>
  );
}

function reasonOf(site: SegmentSite): string {
  if (site.reason !== null) return site.reason;
  return site.lane === 'pending' ? 'судья ещё не смотрел' : 'причина не записана';
}

function WhyCell({ site, clamp, whole }: { site: SegmentSite; clamp: Clamp; whole: boolean }) {
  const reason = reasonOf(site);
  const would = site.change?.would === true ? site.change : null;
  const quote = site.quote !== null && (
    <Text size="xs" fs="italic" ta="center" {...clamped(clamp)} className="judgeQuote">
      «{site.quote}»
    </Text>
  );
  return (
    <Table.Td data-label={WHY.title} className="wrapCell" style={WHOLE_LINE}>
      <Stack gap={2} align="center">
        {whole ? (
          <Text size="sm" ta="center" className="segmentReason">
            {reason}
          </Text>
        ) : (
          <Text size="sm" ta="center" {...clamped(clamp)} title={reason}>
            {reason}
          </Text>
        )}
        {site.quote !== null &&
          (clamp === undefined ? (
            quote
          ) : (
            <Tooltip label={`«${site.quote}»`} withArrow multiline w={360}>
              {quote}
            </Tooltip>
          ))}
        {would?.reason != null && (
          <Text size="xs" c="dimmed" ta="center" {...clamped(clamp)} title={would.reason}>
            стало бы: {would.reason}
          </Text>
        )}
      </Stack>
    </Table.Td>
  );
}

/** Лид — ссылкой на «Лиды» с поиском по домену сайта: там же видно, что с ним сделала очистка. */
function LeadLink({ site }: { site: SegmentSite }) {
  const outcome = site.outcome;
  const state = outcome?.lead_status as LeadState | null | undefined;
  const words = state != null && state in LEAD_STATES ? ` · ${LEAD_STATES[state].title}` : '';
  return (
    <Stack gap={0} align="center">
      <Anchor
        component={Link}
        to={`/sales?search=${encodeURIComponent(site.host)}`}
        size="sm"
        c="var(--ink)"
        underline="always"
        className="inkLink"
      >
        лид{words}
      </Anchor>
      {outcome?.lead_reason != null && (
        <Text size="xs" c="dimmed">
          {leadReasonTitle(outcome.lead_reason)}
        </Text>
      )}
    </Stack>
  );
}

function OutcomeCell({ site, clamp }: { site: SegmentSite; clamp: Clamp }) {
  const outcome = site.outcome;
  let body;
  if (outcome === null) {
    body = (
      <Text size="sm" c="dimmed">
        —
      </Text>
    );
  } else if (outcome.kind === 'lead') {
    body = <LeadLink site={site} />;
  } else {
    body = (
      <Text size="sm" ta="center" {...clamped(clamp)} title={outcome.words}>
        {outcome.words}
      </Text>
    );
  }
  return (
    <Table.Td data-label={OUTCOME.title} className="wrapCell" style={WHOLE_LINE}>
      {body}
    </Table.Td>
  );
}

interface DecideProps {
  site: SegmentSite;
  held: Held | undefined;
  busy: boolean;
  onDecide: (site: SegmentSite, accept: boolean) => void;
}

/** Решённая строка — словами на месте кнопок: «принят — записываем…», потом «решено: принят». */
function heldWords(held: Held): string {
  const lane = LANE_WORDS[held.lane];
  return held.written ? `решено: ${lane}` : `${lane} — записываем…`;
}

function DecideCell({ site, held, busy, onDecide }: DecideProps) {
  return (
    <Table.Td className="cellDecide">
      {held !== undefined ? (
        <Text size="sm" c="dimmed" role="status">
          {heldWords(held)}
        </Text>
      ) : (
        <Group gap="xs" justify="center" wrap="nowrap">
          <Button
            size="compact-sm"
            className="press"
            disabled={busy}
            onClick={() => onDecide(site, true)}
          >
            Принять
          </Button>
          <Button
            size="compact-sm"
            variant="default"
            className="press"
            disabled={busy}
            onClick={() => onDecide(site, false)}
          >
            Отказ
          </Button>
        </Group>
      )}
    </Table.Td>
  );
}

const ALL = 'all';

/** «Кто решил» — под своей колонкой; числа — тем же запросом, что строки. */
function WhoFilter({
  page,
  value,
  onChange,
}: {
  page: SegmentSitesPage;
  value: SiteDecider | null;
  onChange: (decider: SiteDecider | null) => void;
}) {
  const data = [
    { value: ALL, label: 'все' },
    ...DECIDER_KEYS.map((key) => ({
      value: key,
      label: `${DECIDER_WORDS[key]} · ${formatNumber(page.deciders[key] ?? 0)}`,
    })),
  ];
  return (
    <Group justify="center">
      <Select
        size="xs"
        w="9rem"
        aria-label="Кто решил"
        allowDeselect={false}
        value={value ?? ALL}
        data={data}
        onChange={(picked) => onChange(DECIDER_KEYS.find((key) => key === picked) ?? null)}
        comboboxProps={dropdownBelow}
      />
    </Group>
  );
}

interface TableProps {
  tab: SiteTab;
  page: SegmentSitesPage;
  decider: SiteDecider | null;
  onDecider: (decider: SiteDecider | null) => void;
  /** Решения человека в этом виде: строка стоит на месте с пометкой. */
  held: Record<number, Held>;
  /** Строки прежнего вида ждут замены: решать по ним нельзя. */
  stale: boolean;
  onDecide: (site: SegmentSite, accept: boolean) => void;
}

export function SegmentSitesTable({
  tab,
  page,
  decider,
  onDecider,
  held,
  stale,
  onDecide,
}: TableProps) {
  const columns = columnsOf(tab);
  // Решение сервером записано — полоса и «кто решил» уже его; пока пишется — прежние.
  const written = (id: number): SegmentLane | undefined => {
    const found = held[id];
    return found?.written === true ? found.lane : undefined;
  };
  const cards = useMediaQuery(CARDS, false, { getInitialValueInEffect: false }) === true;
  const clamp = cards ? undefined : CLAMP;
  const head = columns.includes(WHO) ? (
    <Table.Tr className="filterRow">
      {columns.map((column) => (
        <Table.Th key={column.title} data-label={column === WHO ? WHO.title : undefined}>
          {column === WHO ? <WhoFilter page={page} value={decider} onChange={onDecider} /> : null}
        </Table.Th>
      ))}
    </Table.Tr>
  ) : undefined;
  return (
    <DataTable
      columns={columns}
      minWidth={minWidthOf(columns)}
      className="filteredTable"
      cardRow
      head={head}
      stale={stale}
    >
      {page.rows.map((site) => (
        <Table.Tr key={site.id} style={CARD_ROW}>
          <SiteCell site={site} clamp={clamp} />
          <LaneCell site={site} decided={written(site.id)} />
          {columns.includes(JUDGE) && <JudgeCell site={site} />}
          {columns.includes(WHO) && (
            <WhoCell site={site} decided={written(site.id) !== undefined} />
          )}
          <WhyCell site={site} clamp={clamp} whole={columns.includes(DECIDE)} />
          {columns.includes(OUTCOME) && <OutcomeCell site={site} clamp={clamp} />}
          {columns.includes(DECIDE) && (
            <DecideCell site={site} held={held[site.id]} busy={stale} onDecide={onDecide} />
          )}
        </Table.Tr>
      ))}
    </DataTable>
  );
}
