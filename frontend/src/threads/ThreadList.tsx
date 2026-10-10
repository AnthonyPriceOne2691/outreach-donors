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
import type { ThreadCard } from '../api/types';
import { Seams } from '../components/Seams';
import { formatMoney, formatNumber } from '../format';
import { LeadsExport } from './LeadsExport';
import { ThreadsTitle } from './ThreadsTitle';
import { isThreadFiltered } from './threadFilters';
import { THREAD_TAB_KEYS, THREAD_TABS, useUnboundPage, writeThreadTab } from './threadTabs';
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

function Row({ thread, to, active }: { thread: ThreadCard; to: string; active: boolean }) {
  const state = threadState(thread.state);
  const price = priceLine(thread);
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
        <Group justify="space-between" wrap="nowrap" gap="sm">
          <Text size="xs" c="dimmed" truncate>
            {thread.contact_email ?? 'адрес не определён'}
          </Text>
          <Badge variant="light" color={state.color} className="threadRowState">
            {state.title}
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

/** Поиск, фильтр состояния и строки — общее у обеих раскладок. */
export function ThreadRows({ list, split }: RowsProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const { data, isLoading, error } = list.query;
  const selected = rowIdOf(useMatch('/threads/:id')?.params.id);
  const box = useRef<HTMLDivElement>(null);

  const href = (thread: ThreadCard) => `/threads/${thread.id}${location.search}`;
  const at = list.shown.findIndex((thread) => thread.id === selected);
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
      <div
        ref={box}
        className={split ? 'threadListRows scrollSlim' : 'threadListRows threadListFlow'}
        role="navigation"
        aria-label="Список диалогов"
        onKeyDown={onArrow}
      >
        {list.shown.map((thread) => (
          <Row
            key={thread.id}
            thread={thread}
            to={href(thread)}
            active={split && thread.id === selected}
          />
        ))}
      </div>
    </>
  );
}

/** Колонка списка рядом с перепиской: шапка, вкладки и строки. */
export function ThreadList() {
  const navigate = useNavigate();
  const list = useThreadList();
  const unbound = useUnboundPage(1);
  const total = list.query.data?.length;
  const switchTab = (next: string) => {
    if (next === 'threads') return;
    // У ответа без письма своей переписки нет: вкладка — на всю ширину, без диалога справа.
    void navigate({ pathname: '/threads', search: writeThreadTab('unbound').toString() });
  };

  return (
    <Stack gap="sm" className="threadListColumn">
      <Group justify="space-between" wrap="nowrap" gap="xs">
        <ThreadsTitle
          total={total}
          found={isThreadFiltered(list.typed) ? list.shown.length : null}
        />
        <LeadsExport compact />
      </Group>
      <SegmentedControl
        aria-label="Вкладки диалогов"
        fullWidth
        size="xs"
        value="threads"
        onChange={switchTab}
        data={THREAD_TAB_KEYS.map((key) => {
          const count = key === 'threads' ? total : unbound.data?.total;
          return {
            value: key,
            label:
              count === undefined
                ? THREAD_TABS[key]
                : `${THREAD_TABS[key]} — ${formatNumber(count)}`,
          };
        })}
      />
      <ThreadRows list={list} split />
    </Stack>
  );
}
