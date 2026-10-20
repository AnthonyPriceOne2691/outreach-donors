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
 * записываем…», пока сервер не ответил, потом «решено: принят» и «Отменить решение»; строки снизу не
 * поднимаются под курсор, и второе нажатие не уходит в соседний сайт. Соседние строки решаются, не
 * дожидаясь ответа. Решение и его отмена — `SegmentSitesDecide.tsx`; на вкладках без колонки
 * «Решение» отмена — под «кто решил».
 *
 * **Причина — словами экрана** (`reasonWords`): отказ судьи в наблюдении — «Отказ: …», и у старых
 * причин тоже. Сайт с ним — «на проверке» с подписью «решает человек»: отказ — мнение судьи. У
 * решения человека под его словами — слова судьи («Судья: …»): видно, с чем он не согласился.
 *
 * **На «На проверку» причина — целиком**: по ней решают (правило «Длинный текст в ячейке не
 * живёт» — кроме того, по чему решают сейчас); на остальных вкладках — две строки, целиком — в
 * подсказке. Причина переносится в своей колонке, кнопки — в своей (находка стенда 14.10.2026).
 */

import { Anchor, Badge, Group, Select, Stack, Table, Text, Tooltip } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { LEAD_STATES, leadReasonTitle } from '../api/salesLabels';
import type { LeadState } from '../api/salesTypes';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { Seams } from '../components/Seams';
import { formatDateTime, formatNumber } from '../format';
import { dropdownBelow } from '../theme';
import { VERDICT_WORDS } from './segmentData';
import type { SegmentSite, SegmentSitesPage, SiteDecider, SiteTab } from './SegmentSitesApi';
import { DecisionSlot } from './SegmentSitesDecide';
import type { Held, SiteAction } from './SegmentSitesDecide';
import {
  DECIDER_KEYS,
  DECIDER_WORDS,
  HUMAN_DECIDES,
  LANE_COLORS,
  LANE_WORDS,
  personOf,
  reasonWords,
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

function LaneCell({ site }: { site: SegmentSite }) {
  const lane = site.lane;
  const change = site.change;
  return (
    <Table.Td data-label={LANE.title} className="wrapCell">
      <Stack gap={2} align="center">
        <Badge variant="light" color={LANE_COLORS[lane]}>
          {LANE_WORDS[lane]}
        </Badge>
        {lane === 'review' && site.verdict === 'reject' && (
          <Text size="xs" c="dimmed">
            {HUMAN_DECIDES}
          </Text>
        )}
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

/** Кто решил и когда; на вкладках без колонки «Решение» под ними — отмена решения человека и
 *  решение по строке, отменённой здесь (`slot`). */
function WhoCell({ site, slot }: { site: SegmentSite; slot: ReactNode }) {
  const who = site.decided_by;
  const author = site.decided_by_user;
  return (
    <Table.Td data-label={WHO.title}>
      <Stack gap={4} align="center">
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
            {site.decided_at !== null && (
              <Text size="xs" c="dimmed">
                {formatDateTime(site.decided_at)}
              </Text>
            )}
          </Stack>
        )}
        {slot}
      </Stack>
    </Table.Td>
  );
}

function reasonOf(site: SegmentSite): string {
  if (site.reason !== null) return reasonWords(site.reason);
  return site.lane === 'pending' ? 'судья ещё не смотрел' : 'причина не записана';
}

/** Приглушённая строка под причиной — слова судьи у решения человека, «стало бы» пересуда; тем же
 *  видом причины (`reasonWords`). Слов нет — строки нет. */
function Aside({
  head,
  words,
  clamp,
}: {
  head: string;
  words: string | null | undefined;
  clamp: Clamp;
}) {
  if (!words) return null;
  const shown = reasonWords(words);
  return (
    <Text size="xs" c="dimmed" ta="center" {...clamped(clamp)} title={shown}>
      {head}: {shown}
    </Text>
  );
}

function WhyCell({ site, clamp, whole }: { site: SegmentSite; clamp: Clamp; whole: boolean }) {
  const reason = reasonOf(site);
  const would = site.change?.would === true ? site.change.reason : null;
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
        {/* На «На проверку» — целиком, как причина: решение здесь только что принято, и по словам
            судьи его отменяют или оставляют. */}
        <Aside head="Судья" words={site.judge_reason} clamp={whole ? undefined : clamp} />
        {site.quote !== null &&
          (clamp === undefined ? (
            quote
          ) : (
            <Tooltip label={`«${site.quote}»`} withArrow multiline w={360}>
              {quote}
            </Tooltip>
          ))}
        <Aside head="стало бы" words={would} clamp={clamp} />
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
  /** Действия человека в этом виде: строка стоит на месте с тем, что записал сервер. */
  held: Record<number, Held>;
  /** Строки прежнего вида ждут замены: решать по ним нельзя. */
  stale: boolean;
  onAct: (site: SegmentSite, action: SiteAction) => void;
}

export function SegmentSitesTable({
  tab,
  page,
  decider,
  onDecider,
  held,
  stale,
  onAct,
}: TableProps) {
  const columns = columnsOf(tab);
  const wide = columns.includes(DECIDE);
  // Строки — уже с тем, что записал сервер (`SegmentSites.rowsOf`): решение или его отмена.
  const slot = (site: SegmentSite, inWide: boolean) => (
    <DecisionSlot site={site} held={held[site.id]} busy={stale} wide={inWide} onAct={onAct} />
  );
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
          <LaneCell site={site} />
          {columns.includes(JUDGE) && <JudgeCell site={site} />}
          {columns.includes(WHO) && <WhoCell site={site} slot={wide ? null : slot(site, false)} />}
          <WhyCell site={site} clamp={clamp} whole={wide} />
          {columns.includes(OUTCOME) && <OutcomeCell site={site} clamp={clamp} />}
          {wide && <Table.Td className="cellDecide">{slot(site, true)}</Table.Td>}
        </Table.Tr>
      ))}
    </DataTable>
  );
}
