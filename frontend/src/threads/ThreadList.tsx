/**
 * Список диалогов — строками в два яруса, на любой ширине (с 09.10.2026).
 *
 * **Строка — в два яруса**, как в очереди «Писем» и в почте: сайт и короткая дата;
 * адрес и состояние. Цена — третьим ярусом, мелко, если она есть; «писем ушло» видно
 * в самой переписке. До этого была таблица в пять колонок: на 1440 «Писем ушло» (у всех
 * 1) и «Цена» (прочерк у 23 из 24) занимали 310 px, а уже 1 200 px таблица уезжала в
 * прокрутку вбок, и домен рвался посреди имени (аудит экранов 09.10.2026).
 *
 * **Широкое окно — колонкой рядом с перепиской** (`ThreadList`, `ThreadsScreen`):
 * выбранная строка подсвечена и названа (`aria-current`), соседний диалог — стрелками
 * ↑/↓ в списке и клавишами J/K откуда угодно, кроме полей ввода: разбирают подряд, не
 * возвращаясь к списку (так у Gmail и Linear). **Узкое окно — на всю ширину**
 * (`ThreadsPage`): строка открывает переписку отдельной страницей, стрелки водят фокус.
 *
 * **Фильтр — в адресе переписки** (`/threads/42?state=…`): «К списку» и соседний
 * диалог берут его оттуда же. Порядок — сначала ждущие человека (`useThreadList`).
 *
 * **Донор или рекламодатель — видно в строке** (слово владельца 14.10.2026): строка
 * рекламодателя помечена тем же словом, что шапка переписки (`THREAD_STAGE_NOTES`), а
 * переключатель «Все · Доноры · Рекламодатели» над поиском сужает список (`?role=`). До
 * этого письмо рекламодателю в списке не отличалось от письма донору. В части списка —
 * «Диалогах с донорами», «с рекламодателями», «продаж» — переключателя и пометки нет: там
 * одна сторона, и её называет заголовок.
 *
 * **Адрес во втором ярусе читается при любой метке состояния** (11.11.2026). Метка не
 * сжимается, и длинная — «ответ продаж — ждёт человека», «цену перезапросили — ждём ответа» —
 * оставляла от адреса семь знаков, а то и два (разведка экранов 10.11.2026). Адресу теперь
 * положено место (`threadRowWho`): метка, которой рядом с ним тесно, встаёт строкой ниже, у
 * правого края. В части списка метка ещё и не повторяет слово части (`stateTitle`).
 *
 * **Рисуются первые сто строк**, дальше — «Показать ещё» (`listWindow.ts`): список приходит
 * целиком, а тысяча строк разом — лента в полтора десятка экранов на телефоне.
 *
 * **Колонка — с той же шапкой на обеих вкладках** (`ThreadList`): заголовок части и
 * переключатель вкладок стоят в одной карточке и на одном месте, под ними — строки диалогов
 * или ответы без письма. Смена вкладки двигает только то, что под переключателем.
 */

import {
  Badge,
  Button,
  Card,
  Group,
  Select,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
} from '@mantine/core';
import { useHotkeys } from '@mantine/hooks';
import { useEffect, useRef } from 'react';
import type { KeyboardEvent } from 'react';
import { Link, useLocation, useMatch, useNavigate } from 'react-router-dom';

import { rowIdOf } from '../api/ids';
import { threadState } from '../api/labels';
import { THREAD_STAGE_NOTES } from '../api/stages';
import type { Part } from '../api/stages';
import type { ThreadCard } from '../api/types';
import { usePageParam } from '../components/PageSwitch';
import { Seams } from '../components/Seams';
import { formatMoney, formatNumber } from '../format';
import { LeadsExport } from './LeadsExport';
import { LIST_STEP, listScope, useListWindow } from './listWindow';
import { ThreadsTabs } from './ThreadsTabs';
import { ThreadsTitle } from './ThreadsTitle';
import { hasLeads, isThreadFiltered, stateTitle, THREAD_ROLES } from './threadFilters';
import type { ThreadTab } from './threadTabs';
import { UnboundReplies } from './UnboundReplies';
import { useThreadList } from './useThreadList';
import type { ThreadListState } from './useThreadList';

const LOCALE = 'ru-RU';

/** Дата строки — коротко: время сегодняшнего, день и месяц этого года, полная —
 *  прошлых лет. Полная «08.10.2026, 14:09» в узкой колонке теснила адрес. */
export function shortWhen(moment: string | null, now = new Date()): string {
  if (moment === null || moment === '') return '—';
  const at = new Date(moment);
  if (at.toDateString() === now.toDateString()) {
    return at.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
  }
  if (at.getFullYear() === now.getFullYear()) {
    return at.toLocaleDateString(LOCALE, { day: '2-digit', month: '2-digit' });
  }
  return at.toLocaleDateString(LOCALE);
}

/** Цены — одной строкой и названные: «белая 250,00 € · серая 180,00 €». */
function priceLine(thread: ThreadCard): string | null {
  const parts = [
    thread.price_white === null
      ? null
      : `белая ${formatMoney(thread.price_white, thread.currency)}`,
    thread.price_grey === null ? null : `серая ${formatMoney(thread.price_grey, thread.currency)}`,
  ].filter((part) => part !== null);
  return parts.length === 0 ? null : parts.join(' · ');
}

/** Переключатель «Кому писали»: «Все» — без сужения, в адрес не пишется. */
const ROLE_OPTIONS = [
  { value: 'all', label: 'Все' },
  { value: 'donors', label: THREAD_ROLES.donors },
  { value: 'advertisers', label: THREAD_ROLES.advertisers },
];

interface RowProps {
  thread: ThreadCard;
  to: string;
  active: boolean;
  /** Часть списка. Без части доноры и рекламодатели идут вперемешку, и рекламодатель помечен;
   *  в части метка состояния не повторяет её слово (`stateTitle`). */
  part: Part | null;
}

function Row({ thread, to, active, part }: RowProps) {
  const price = priceLine(thread);
  // Пометка — у рекламодателя: доноры — основной поток, а у продаж своя часть списка.
  const note = part === null && thread.stage !== 'sales' ? THREAD_STAGE_NOTES[thread.stage] : '';
  return (
    <Card
      component={Link}
      to={to}
      p="sm"
      className={`threadRow press ${active ? 'glassQuiet' : 'glassSlot liftable'}`}
      aria-current={active ? 'page' : undefined}
      data-thread={thread.id}
    >
      <Stack gap={2}>
        <Group justify="space-between" wrap="nowrap" gap="sm" align="baseline">
          <Text fw={active ? 600 : 500} className="cellName threadRowHost">
            <Seams text={thread.host} />
          </Text>
          <Text size="xs" c="dimmed" className="threadRowWhen">
            {shortWhen(thread.last_event_at)}
          </Text>
        </Group>
        {/* Ярус переносится: метка состояния, которой рядом с адресом тесно, встаёт строкой
            ниже (`threadRowWho` держит адресу место), а не сжимает адрес до семи знаков. */}
        <Group justify="space-between" gap="sm" className="threadRowSecond" style={{ rowGap: 4 }}>
          {/* Адрес обрезается, пометка — нет: длинный адрес не прячет, кому писали. */}
          <Group gap={4} wrap="nowrap" className="threadRowWho">
            <Text size="xs" c="dimmed" truncate>
              {thread.contact_email ?? 'адрес не определён'}
            </Text>
            {note !== '' && (
              <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
                · {note}
              </Text>
            )}
          </Group>
          <Badge variant="light" color={threadState(thread.state).color} className="threadRowState">
            {stateTitle(thread.state, part)}
          </Badge>
        </Group>
        {price !== null && (
          <Text size="xs" className="threadRowPrice">
            {price}
          </Text>
        )}
      </Stack>
    </Card>
  );
}

interface RowsProps {
  list: ThreadListState;
  /** Колонкой рядом с перепиской: строка выбирается, а не открывает страницу. */
  split: boolean;
}

/** «Кому писали» над поиском — только у списка без части: в части одна сторона. */
function RoleSwitch({ list }: { list: ThreadListState }) {
  if (list.filters.part !== null) return null;
  return (
    <SegmentedControl
      aria-label="Кому писали"
      fullWidth
      size="xs"
      value={list.filters.role ?? 'all'}
      onChange={list.pickRole}
      data={ROLE_OPTIONS}
    />
  );
}

/** Поиск и фильтр состояния над строками. */
function ListFilters({ list }: { list: ThreadListState }) {
  return (
    <Group gap="xs" wrap="nowrap" className="threadFilters">
      {/* Подсказка — одним словом, что ищется — в имени поля: рядом с перепиской на
          1280 колонка списка — 20rem, и поиску за фильтром состояния остаётся ~80 px
          текста. «Донор или адрес» обрезалось до «Донор или ад» (проверка QA 10.10.2026). */}
      <TextInput
        size="xs"
        placeholder="Поиск"
        aria-label="Поиск по донору или адресу"
        value={list.search}
        onChange={(event) => list.setSearch(event.currentTarget.value)}
        className="threadSearch"
      />
      <Select
        size="xs"
        aria-label="Состояние"
        allowDeselect={false}
        value={list.filters.state ?? 'all'}
        onChange={list.pickState}
        data={list.stateOptions}
        comboboxProps={{ width: 'max-content', position: 'bottom-end' }}
        className="threadStateFilter"
      />
    </Group>
  );
}

/** Вместо строк: список грузится, не загрузился или под фильтр ничего не попало. */
function ListNotes({ list }: { list: ThreadListState }) {
  const { data, isLoading, error } = list.query;
  return (
    <>
      {isLoading && (
        <Text size="sm" c="dimmed">
          Загружаем диалоги…
        </Text>
      )}
      {error !== null && (
        <Text size="sm" c="red">
          Диалоги не загрузились
        </Text>
      )}
      {list.empty !== null && data !== undefined && (
        <Stack gap={6} align="flex-start" py="xs">
          <Text size="sm" fw={500}>
            {list.empty.title}
          </Text>
          <Text size="sm" c="dimmed">
            {list.empty.detail}
          </Text>
          {isThreadFiltered(list.typed) && (
            <Button variant="subtle" size="compact-sm" className="press" onClick={list.reset}>
              Сбросить фильтры
            </Button>
          )}
        </Stack>
      )}
    </>
  );
}

/** «Показать ещё 100» под показанными строками и сколько осталось за ними; осталось меньше
 *  сотни — кнопка называет остаток, и вторая цифра рядом была бы той же. */
function MoreRows({ rest, onMore }: { rest: number; onMore: () => void }) {
  if (rest <= 0) return null;
  return (
    <Group gap="sm" justify="center" wrap="nowrap" className="threadListMore">
      <Button variant="subtle" size="compact-sm" className="press" onClick={onMore}>
        {`Показать ещё\u00a0${formatNumber(Math.min(LIST_STEP, rest))}`}
      </Button>
      {rest > LIST_STEP && (
        <Text size="xs" c="dimmed">
          {`осталось\u00a0${formatNumber(rest)}`}
        </Text>
      )}
    </Group>
  );
}

/** Поиск, фильтр состояния и строки — общее у обеих раскладок. */
export function ThreadRows({ list, split }: RowsProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const selected = rowIdOf(useMatch('/threads/:id')?.params.id);
  const box = useRef<HTMLDivElement>(null);

  const href = (thread: ThreadCard) => `/threads/${thread.id}${location.search}`;
  const at = list.shown.findIndex((thread) => thread.id === selected);
  // Нарисованы первые сто и — до открытого диалога; соседний диалог (J, K, стрелки) и
  // счётчики берутся из всего списка, а не из нарисованного.
  const drawn = useListWindow(listScope(list.typed), at, list.shown.length);
  const step = (by: 1 | -1) => {
    const next = at === -1 ? list.shown[0] : list.shown[at + by];
    if (next !== undefined) void navigate(href(next));
  };
  // По физической клавише: в русской раскладке J и K — «о» и «л». Только рядом с
  // перепиской: на узком окне J открывала бы диалог, уводя со списка.
  useHotkeys(
    split
      ? [
          ['j', () => step(1), { preventDefault: true, usePhysicalKeys: true }],
          ['k', () => step(-1), { preventDefault: true, usePhysicalKeys: true }],
        ]
      : [],
  );
  const onArrow = (event: KeyboardEvent) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    const by = event.key === 'ArrowDown' ? 1 : -1;
    if (split) {
      step(by);
      return;
    }
    // Узкое окно: стрелки ведут фокус по строкам, открывает — Enter.
    const rows = [...(box.current?.querySelectorAll<HTMLElement>('[data-thread]') ?? [])];
    const from = rows.findIndex((row) => row === document.activeElement);
    rows[from === -1 ? 0 : from + by]?.focus();
  };

  // Выбранная строка — в виду: диалог открыли по ссылке или клавишей, а строка ниже
  // края колонки. Фокус идёт за выбором, если он был в списке: стрелки листают дальше.
  useEffect(() => {
    if (!split) return;
    const row = box.current?.querySelector<HTMLElement>(`[data-thread="${String(selected)}"]`);
    row?.scrollIntoView?.({ block: 'nearest' });
    if (row && box.current?.contains(document.activeElement)) row.focus();
  }, [split, selected, list.shown.length]);

  return (
    <>
      <RoleSwitch list={list} />
      <ListFilters list={list} />
      <ListNotes list={list} />
      <div
        ref={box}
        className={split ? 'threadListRows scrollSlim' : 'threadListRows threadListFlow'}
        role="navigation"
        aria-label="Список диалогов"
        onKeyDown={onArrow}
      >
        {list.shown.slice(0, drawn.count).map((thread) => (
          <Row
            key={thread.id}
            thread={thread}
            to={href(thread)}
            active={split && thread.id === selected}
            part={list.filters.part}
          />
        ))}
        <MoreRows rest={drawn.rest} onMore={drawn.more} />
      </div>
    </>
  );
}

/**
 * Колонка списка на широком окне: шапка — заголовок части и вкладки — и под ней строки диалогов
 * или ответы без письма. Шапка одна на обе вкладки и стоит в той же карточке на том же месте
 * (`threadsHead`): до 11.11.2026 «Не привязаны» рисовала страница списка со своей шапкой, и
 * переключатель под указателем уезжал на 21–55 px (разведка экранов 10.11.2026).
 */
export function ThreadList({ tab }: { tab: ThreadTab }) {
  const list = useThreadList();
  const [page, goToPage] = usePageParam();
  const part = list.filters.part;

  return (
    <Stack gap="sm" className="threadListColumn">
      <Stack gap="sm" className="threadsHead">
        {/* Лиды файлом — ответы на оффер рекламодателю: у доноров и продаж их нет. */}
        <ThreadsTitle
          total={list.total}
          found={isThreadFiltered(list.typed) ? list.shown.length : null}
          part={part}
          compact
          aside={hasLeads(part) ? <LeadsExport compact /> : undefined}
          tab={tab}
        />
        <ThreadsTabs tab={tab} list={list} column />
      </Stack>
      {tab === 'unbound' ? (
        // У ответа без письма своей переписки нет: вкладка — на всю ширину карточки.
        <div className="threadsWholeBody">
          <UnboundReplies page={page} onPage={goToPage} />
        </div>
      ) : (
        <ThreadRows list={list} split />
      )}
    </Stack>
  );
}
